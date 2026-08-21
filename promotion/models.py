from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone

from app.base.models import BaseModel
from promotion.choices import DiscountDuration, DiscountType


class Discount(BaseModel):
    """The monetary benefit behind one or more customer-facing coupons."""

    name = models.CharField(max_length=120)
    discount_type = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
    )
    value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Percentage points or a fixed monetary amount.",
    )
    currency = models.CharField(
        max_length=3,
        blank=True,
        help_text="Required for fixed discounts and blank for percentage discounts.",
    )
    duration = models.CharField(
        max_length=12,
        choices=DiscountDuration.choices,
        default=DiscountDuration.ONCE,
    )
    duration_in_billing_cycles = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Required only for repeating discounts.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(value__gt=0),
                name="promo_discount_value_gt_0",
            ),
            models.CheckConstraint(
                condition=~Q(discount_type=DiscountType.PERCENTAGE)
                | Q(value__lte=100),
                name="promo_discount_percent_lte",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        duration=DiscountDuration.REPEATING,
                        duration_in_billing_cycles__isnull=False,
                    )
                    | (
                        ~Q(duration=DiscountDuration.REPEATING)
                        & Q(duration_in_billing_cycles__isnull=True)
                    )
                ),
                name="promo_discount_cycles_valid",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        self.currency = self.currency.strip().upper()

        if self.discount_type == DiscountType.PERCENTAGE:
            if self.value > 100:
                raise ValidationError(
                    {"value": "A percentage discount cannot exceed 100%."}
                )
            if self.currency:
                raise ValidationError(
                    {"currency": "Percentage discounts do not use a currency."}
                )
        elif not self.currency:
            raise ValidationError(
                {"currency": "A fixed-amount discount requires a currency."}
            )

        if self.duration == DiscountDuration.REPEATING:
            if not self.duration_in_billing_cycles:
                raise ValidationError(
                    {
                        "duration_in_billing_cycles": (
                            "A repeating discount requires at least one billing cycle."
                        )
                    }
                )
        elif self.duration_in_billing_cycles is not None:
            raise ValidationError(
                {
                    "duration_in_billing_cycles": (
                        "Only repeating discounts use a billing-cycle count."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.currency = self.currency.strip().upper()
        super().save(*args, **kwargs)


class Coupon(BaseModel):
    """A redeemable code controlling access to a discount."""

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=120, blank=True)
    discount = models.ForeignKey(
        Discount,
        on_delete=models.PROTECT,
        related_name="coupons",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    valid_from = models.DateTimeField(default=timezone.now, db_index=True)
    valid_until = models.DateTimeField(null=True, blank=True, db_index=True)
    max_redemptions = models.PositiveIntegerField(null=True, blank=True)
    max_redemptions_per_user = models.PositiveIntegerField(null=True, blank=True)
    minimum_purchase_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    minimum_purchase_currency = models.CharField(max_length=3, blank=True)
    first_payment_only = models.BooleanField(default=False)
    applies_to_all_plans = models.BooleanField(default=True)
    eligible_plans = models.ManyToManyField(
        "subscription.SubscriptionPlan",
        blank=True,
        related_name="eligible_coupons",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                Lower("code"),
                name="promo_coupon_code_unique",
            ),
            models.CheckConstraint(
                condition=Q(minimum_purchase_amount__isnull=True)
                | Q(minimum_purchase_amount__gte=0),
                name="promo_coupon_min_amount_gte_0",
            ),
        ]

    def __str__(self):
        return self.code

    def clean(self):
        super().clean()
        self.code = self.code.strip().upper()
        self.minimum_purchase_currency = (
            self.minimum_purchase_currency.strip().upper()
        )

        if self.valid_until and self.valid_until <= self.valid_from:
            raise ValidationError(
                {"valid_until": "The coupon must expire after its start time."}
            )
        if self.minimum_purchase_amount is not None:
            if not self.minimum_purchase_currency:
                raise ValidationError(
                    {
                        "minimum_purchase_currency": (
                            "A minimum purchase amount requires a currency."
                        )
                    }
                )
        elif self.minimum_purchase_currency:
            raise ValidationError(
                {
                    "minimum_purchase_currency": (
                        "Set a minimum purchase amount before setting its currency."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        self.minimum_purchase_currency = (
            self.minimum_purchase_currency.strip().upper()
        )
        super().save(*args, **kwargs)


class CouponRedemption(BaseModel):
    """A coupon application with an immutable discount and price snapshot."""

    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.PROTECT,
        related_name="redemptions",
    )
    subscription = models.ForeignKey(
        "subscription.ChatbotSubscription",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coupon_redemptions",
    )
    payment = models.OneToOneField(
        "subscription.Payment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coupon_redemption",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coupon_redemptions",
    )

    coupon_code_snapshot = models.CharField(max_length=50, editable=False)
    discount_name_snapshot = models.CharField(max_length=120, editable=False)
    discount_type_snapshot = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
        editable=False,
    )
    discount_value_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        editable=False,
    )
    discount_currency_snapshot = models.CharField(
        max_length=3,
        blank=True,
        editable=False,
    )
    discount_duration_snapshot = models.CharField(
        max_length=12,
        choices=DiscountDuration.choices,
        editable=False,
    )
    duration_in_billing_cycles_snapshot = models.PositiveIntegerField(
        null=True,
        editable=False,
    )
    billing_cycles_remaining = models.PositiveIntegerField(null=True, blank=True)

    original_amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2)
    final_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    is_active = models.BooleanField(default=True, db_index=True)
    redeemed_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-redeemed_at"]
        indexes = [
            models.Index(
                fields=["coupon", "user", "-redeemed_at"],
                name="promo_redeem_user_idx",
            ),
            models.Index(
                fields=["subscription", "is_active"],
                name="promo_redeem_active_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(original_amount__gte=0)
                & Q(discount_amount__gte=0)
                & Q(final_amount__gte=0)
                & Q(discount_amount__lte=F("original_amount")),
                name="promo_redeem_amounts_valid",
            ),
            models.CheckConstraint(
                condition=Q(final_amount=F("original_amount") - F("discount_amount")),
                name="promo_redeem_total_valid",
            ),
        ]

    def __str__(self):
        return f"{self.coupon_code_snapshot} - {self.final_amount} {self.currency}"

    def capture_discount_snapshot(self):
        discount = self.coupon.discount
        self.coupon_code_snapshot = self.coupon.code
        self.discount_name_snapshot = discount.name
        self.discount_type_snapshot = discount.discount_type
        self.discount_value_snapshot = discount.value
        self.discount_currency_snapshot = discount.currency
        self.discount_duration_snapshot = discount.duration
        self.duration_in_billing_cycles_snapshot = (
            discount.duration_in_billing_cycles
        )
        if discount.duration == DiscountDuration.REPEATING:
            self.billing_cycles_remaining = discount.duration_in_billing_cycles
        elif discount.duration == DiscountDuration.ONCE:
            self.billing_cycles_remaining = 1
        else:
            self.billing_cycles_remaining = None

    def validate_discount_snapshot(self):
        errors = {}
        if self.discount_type_snapshot == DiscountType.FIXED_AMOUNT:
            if self.discount_currency_snapshot != self.currency:
                errors["currency"] = (
                    "A fixed discount must use the redemption currency."
                )
        elif self.discount_currency_snapshot:
            errors["discount_currency_snapshot"] = (
                "A percentage discount cannot have a currency."
            )

        if self.discount_amount > self.original_amount:
            errors["discount_amount"] = (
                "The discount cannot exceed the original amount."
            )
        if self.final_amount != self.original_amount - self.discount_amount:
            errors["final_amount"] = (
                "Final amount must equal original minus discount."
            )
        if errors:
            raise ValidationError(errors)

    def validate_snapshot_immutability(self):
        if self._state.adding or not self.pk:
            return

        snapshot_fields = (
            "coupon_id",
            "coupon_code_snapshot",
            "discount_name_snapshot",
            "discount_type_snapshot",
            "discount_value_snapshot",
            "discount_currency_snapshot",
            "discount_duration_snapshot",
            "duration_in_billing_cycles_snapshot",
        )
        original = type(self).objects.filter(pk=self.pk).values(*snapshot_fields).first()
        if original is None:
            return
        if any(getattr(self, field) != original[field] for field in snapshot_fields):
            raise ValidationError(
                {
                    "coupon": (
                        "A redeemed coupon's discount snapshot cannot be changed."
                    )
                }
            )

    def clean(self):
        super().clean()
        self.currency = self.currency.strip().upper()
        if self._state.adding and self.coupon_id:
            self.capture_discount_snapshot()
        if self.expires_at and self.expires_at <= self.redeemed_at:
            raise ValidationError(
                {"expires_at": "A redemption must expire after it is redeemed."}
            )
        self.validate_discount_snapshot()
        self.validate_snapshot_immutability()

    def save(self, *args, **kwargs):
        self.currency = self.currency.strip().upper()
        if self._state.adding:
            self.capture_discount_snapshot()
        self.validate_discount_snapshot()
        self.validate_snapshot_immutability()
        super().save(*args, **kwargs)
