from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.text import slugify

from app.base.models import BaseMinModel, BaseModel
from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    PaymentStatus,
    PaymentType,
    PlanFeature,
    PlanType,
    RenewalMode,
    SubscriptionStatus,
    WebhookProcessingStatus,
)
from subscription.validators import validate_subscription_snapshot


class SubscriptionPlan(BaseModel):
    """A selectable plan and the usage limits included with it.

    Pricing is intentionally NOT here — see PlanPrice. A plan is a bundle of
    limits/features; how much it costs on which provider/currency is a
    separate, provider-aware concern.
    """

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True, editable=False)
    plan_type = models.CharField(
        max_length=20,
        choices=PlanType.choices,
        default=PlanType.STANDARD,
        db_index=True,
    )

    ai_message_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Included messages for a monthly billing period. Annual subscription "
            "snapshots receive twelve times this allowance."
        ),
    )
    file_size_limit_mb = models.PositiveIntegerField(null=True, blank=True)
    knowledge_chunk_limit = models.PositiveIntegerField(null=True, blank=True)
    ai_message_overage_enabled = models.BooleanField(
        default=False,
        help_text="Allow pay-per-use AI messages after the included limit is used.",
    )

    features = ArrayField(
        base_field=models.CharField(max_length=40, choices=PlanFeature.choices),
        default=list,
        blank=True,
    )
    details_html = models.TextField(
        blank=True,
        help_text="Sanitize at render time, not at save time.",
    )

    is_free = models.BooleanField(default=False, db_index=True)
    is_public = models.BooleanField(default=True, db_index=True)
    requires_sales_contact = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(
                fields=["is_active", "is_public", "sort_order"],
                name="sub_plan_public_idx",
            ),
            models.Index(fields=["plan_type", "is_active"], name="sub_plan_type_idx"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        features = self.features or []
        if len(features) != len(set(features)):
            raise ValidationError({"features": "Plan features must be unique."})
        if (
            self.ai_message_overage_enabled
            and self.ai_message_limit is None
        ):
            raise ValidationError(
                {
                    "ai_message_limit": (
                        "An unlimited plan cannot use message overage billing."
                    )
                }
            )

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)[:120].strip("-") or "plan"
            candidate = base_slug
            suffix = 2
            queryset = type(self).objects.all()
            while queryset.filter(slug=candidate).exists():
                suffix_text = f"-{suffix}"
                candidate = f"{base_slug[: 140 - len(suffix_text)]}{suffix_text}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)


class PlanPrice(BaseModel):
    """A concrete, purchasable price point for a plan on a given provider.

    One plan -> many prices: (Stripe, monthly, USD), (Stripe, annual, USD),
    (bKash, monthly, BDT), etc. This is what checkout actually references,
    not SubscriptionPlan directly.
    """

    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="prices"
    )
    provider = models.CharField(max_length=20, choices=PaymentProvider.choices)
    billing_interval = models.CharField(max_length=10, choices=BillingInterval.choices)
    currency = models.CharField(max_length=3, default="USD")
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    ai_message_overage_unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Price for each AI message above the plan limit.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "provider", "billing_interval", "currency"],
                name="sub_planprice_unique_combo",
            ),
            models.CheckConstraint(condition=Q(amount__gte=0), name="sub_planprice_amount_gte_0"),
            models.CheckConstraint(
                condition=Q(ai_message_overage_unit_price__isnull=True)
                | Q(ai_message_overage_unit_price__gte=0),
                name="sub_planprice_ppu_gte_0",
            ),
        ]

    def __str__(self):
        return f"{self.plan} - {self.provider} {self.billing_interval} {self.amount} {self.currency}"

    def clean(self):
        super().clean()
        self.currency = self.currency.strip().upper()

        if self.plan.is_free and self.amount != Decimal("0"):
            raise ValidationError(
                {"amount": "A free plan price must have a zero amount."}
            )

        if self.plan.ai_message_overage_enabled:
            if self.ai_message_overage_unit_price is None:
                raise ValidationError(
                    {
                        "ai_message_overage_unit_price": (
                            "An overage-enabled plan requires a per-message price."
                        )
                    }
                )
        elif self.ai_message_overage_unit_price is not None:
            raise ValidationError(
                {
                    "ai_message_overage_unit_price": (
                        "Enable AI message overage on the plan before setting a price."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.currency = self.currency.strip().upper()
        super().save(*args, **kwargs)


class ChatbotSubscription(BaseModel):
    """The current subscription selected for one chatbot.

    This model is the source of truth for entitlement (what the chatbot is
    allowed to do right now) — never trust a live call to Stripe/bKash for
    that on the request path. Providers only ever update this via webhook
    handlers or the renewal job, both server-to-server.
    """

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan_price = models.ForeignKey(
        PlanPrice, on_delete=models.PROTECT, related_name="chatbot_subscriptions"
    )
    selected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="selected_chatbot_subscriptions",
    )

    # Versioned immutable contract. Billing and entitlement code should use
    # accessors below instead of reading mutable Plan/PlanPrice records.
    snapshot = models.JSONField(
        default=dict,
        editable=False,
        validators=[validate_subscription_snapshot],
    )

    provider = models.CharField(max_length=20, choices=PaymentProvider.choices)
    # PROVIDER_MANAGED: trust provider webhooks to advance the period (Stripe).
    # APP_MANAGED: our own scheduled job charges and advances the period (bKash).
    renewal_mode = models.CharField(max_length=20, choices=RenewalMode.choices)

    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.INCOMPLETE,
        db_index=True,
    )

    started_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True, db_index=True)
    # Only meaningful for APP_MANAGED providers — when the renewal job should
    # next attempt a charge. Indexed since the scheduler queries on it.
    next_billing_at = models.DateTimeField(null=True, blank=True, db_index=True)
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # Generic across providers: Stripe customer id, bKash wallet/payer
    # reference, etc.
    provider_customer_id = models.CharField(max_length=255, blank=True)
    # Only providers with a real subscription object populate this (Stripe).
    # For bKash this stays blank — the "subscription" only exists in our DB.
    provider_subscription_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    provider_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "current_period_end"], name="sub_status_period_idx"),
            models.Index(fields=["provider", "provider_customer_id"], name="sub_provider_cust_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["chatbot"],
                condition=Q(
                    status__in=[
                        SubscriptionStatus.INCOMPLETE,
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.PAST_DUE,
                        SubscriptionStatus.PAUSED,
                        SubscriptionStatus.UNPAID,
                    ]
                ),
                name="sub_one_open_per_chatbot",
            ),
            models.UniqueConstraint(
                fields=["provider", "provider_subscription_id"],
                condition=Q(
                    status__in=[
                        SubscriptionStatus.INCOMPLETE,
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.PAST_DUE,
                        SubscriptionStatus.PAUSED,
                        SubscriptionStatus.UNPAID,
                    ],
                    provider_subscription_id__isnull=False,
                )
                & ~Q(provider_subscription_id=""),
                name="sub_provider_subscription_unique",
            ),
        ]

    def __str__(self):
        return f"{self.chatbot} - {self.get_plan_name()} ({self.get_status_display()})"

    def capture_plan_snapshot(self):
        """Copy the selected plan contract without retaining mutable references."""
        plan_price = self.plan_price
        plan = plan_price.plan

        self.provider = plan_price.provider
        ai_message_limit = plan.ai_message_limit
        if (
            ai_message_limit is not None
            and plan_price.billing_interval == BillingInterval.ANNUAL
        ):
            ai_message_limit *= 12
        overage_enabled = plan.ai_message_overage_enabled

        self.snapshot = {
            "version": 1,
            "plan": {
                "id": str(plan.pk),
                "name": plan.name,
                "type": str(plan.plan_type),
                "is_free": plan.is_free,
                "details_html": plan.details_html,
                "features": list(plan.features or []),
            },
            "pricing": {
                "plan_price_id": str(plan_price.pk),
                "provider": str(plan_price.provider),
                "billing_interval": str(plan_price.billing_interval),
                "amount": str(plan_price.amount),
                "currency": plan_price.currency.strip().upper(),
            },
            "limits": {
                "ai_message_limit": ai_message_limit,
                "file_size_limit_mb": plan.file_size_limit_mb,
                "knowledge_chunk_limit": plan.knowledge_chunk_limit,
            },
            "overage": {
                "enabled": overage_enabled,
                "unit_price": (
                    str(plan_price.ai_message_overage_unit_price)
                    if (
                        overage_enabled
                        and plan_price.ai_message_overage_unit_price is not None
                    )
                    else None
                ),
            },
        }

    def validate_contract_snapshot(self):
        """Validate the copied contract used by entitlement and billing code."""
        validate_subscription_snapshot(self.snapshot)
        errors = {}
        if self.current_period_start and self.current_period_end:
            if self.current_period_end <= self.current_period_start:
                errors["current_period_end"] = (
                    "The billing period must end after it starts."
                )

        if self.provider != self.snapshot["pricing"]["provider"]:
            errors["provider"] = "Provider must match the contract snapshot."
        if str(self.plan_price_id) != self.snapshot["pricing"]["plan_price_id"]:
            errors["plan_price"] = "Plan price must match the contract snapshot."

        if errors:
            raise ValidationError(errors)

    def validate_snapshot_immutability(self):
        """Prevent accidental edits to a contract after it has been selected."""
        if self._state.adding or not self.pk:
            return

        snapshot_fields = ("plan_price_id", "provider", "snapshot")
        original = type(self).objects.filter(pk=self.pk).values(*snapshot_fields).first()
        if original is None:
            return

        changed_fields = [
            field
            for field in snapshot_fields
            if getattr(self, field) != original[field]
        ]
        if changed_fields:
            raise ValidationError(
                {
                    "plan_price": (
                        "A subscription contract snapshot is immutable. Create a new "
                        "contract when changing plans or prices."
                    )
                }
            )

    def _snapshot_section(self, section):
        value = (self.snapshot or {}).get(section, {})
        return value if isinstance(value, dict) else {}

    def get_plan_name(self):
        return self._snapshot_section("plan").get("name", "")

    def get_plan_type(self):
        return self._snapshot_section("plan").get("type")

    def get_plan_details_html(self):
        return self._snapshot_section("plan").get("details_html", "")

    def get_features(self):
        return list(self._snapshot_section("plan").get("features", []))

    def has_feature(self, feature):
        feature_value = getattr(feature, "value", feature)
        return feature_value in self.get_features()

    def is_free_plan(self):
        return bool(self._snapshot_section("plan").get("is_free", False))

    def get_billing_interval(self):
        return self._snapshot_section("pricing").get("billing_interval")

    def get_price_amount(self):
        value = self._snapshot_section("pricing").get("amount")
        return Decimal(str(value)) if value is not None else None

    def get_currency(self):
        return self._snapshot_section("pricing").get("currency")

    def get_ai_message_limit(self):
        return self._snapshot_section("limits").get("ai_message_limit")

    def get_file_size_limit_mb(self):
        return self._snapshot_section("limits").get("file_size_limit_mb")

    def get_knowledge_chunk_limit(self):
        return self._snapshot_section("limits").get("knowledge_chunk_limit")

    def is_ai_message_overage_enabled(self):
        return bool(self._snapshot_section("overage").get("enabled", False))

    def get_ai_message_overage_unit_price(self):
        value = self._snapshot_section("overage").get("unit_price")
        return Decimal(str(value)) if value is not None else None

    def clean(self):
        super().clean()
        if self._state.adding and self.plan_price_id:
            self.capture_plan_snapshot()
        self.validate_contract_snapshot()
        self.validate_snapshot_immutability()

    def calculate_ai_message_overage(self, message_count):
        """Return ``(overage_messages, overage_cost)`` for one billing period."""
        if message_count < 0:
            raise ValidationError("AI message usage cannot be negative.")

        included = self.get_ai_message_limit()
        if included is None or message_count <= included:
            return 0, Decimal("0")

        overage_messages = message_count - included
        if not self.is_ai_message_overage_enabled():
            raise ValidationError("This subscription does not allow message overage.")

        unit_price = self.get_ai_message_overage_unit_price()
        if unit_price is None:
            raise ValidationError("The subscription has no overage unit price.")
        return overage_messages, unit_price * overage_messages

    def full_clean(self, *args, **kwargs):
        if self._state.adding and self.plan_price_id:
            self.capture_plan_snapshot()
        return super().full_clean(*args, **kwargs)

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.capture_plan_snapshot()
        self.validate_contract_snapshot()
        self.validate_snapshot_immutability()
        super().save(*args, **kwargs)


class Payment(BaseModel):
    """A payment attempt, recorded independently from mutable provider objects."""

    subscription = models.ForeignKey(
        ChatbotSubscription, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments"
    )
    plan_price = models.ForeignKey(
        PlanPrice, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="subscription_payments",
    )

    provider = models.CharField(max_length=20, choices=PaymentProvider.choices)
    payment_type = models.CharField(max_length=20, choices=PaymentType.choices, default=PaymentType.SUBSCRIPTION)
    status = models.CharField(max_length=30, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True)
    billing_interval = models.CharField(max_length=10, choices=BillingInterval.choices, null=True, blank=True)

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_refunded = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=3, default="USD")
    description = models.CharField(max_length=255, blank=True)

    provider_customer_id = models.CharField(max_length=255, blank=True)
    # The single most useful id to search by for this payment (checkout
    # session id, payment intent id, bKash paymentID — whichever the
    # provider treats as canonical for that flow). Everything else
    # (charge id, invoice id, raw payload) goes in metadata.
    provider_reference = models.CharField(max_length=255, null=True, blank=True)
    provider_metadata = models.JSONField(default=dict, blank=True)

    idempotency_key = models.CharField(
        max_length=255, unique=True, null=True, blank=True,
        help_text="App-generated key preventing duplicate payment attempts.",
    )

    failure_code = models.CharField(max_length=100, blank=True)
    failure_message = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status", "-created_at"], name="sub_payment_user_idx"),
            models.Index(fields=["subscription", "-created_at"], name="sub_payment_sub_idx"),
            models.Index(fields=["provider", "provider_customer_id"], name="sub_payment_provider_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_reference"],
                condition=Q(provider_reference__isnull=False)
                & ~Q(provider_reference=""),
                name="sub_payment_provider_ref_unique",
            ),
            models.CheckConstraint(condition=Q(amount__gte=0), name="sub_payment_amount_gte_0"),
            models.CheckConstraint(
                condition=Q(amount_refunded__gte=0) & Q(amount_refunded__lte=models.F("amount")),
                name="sub_payment_refund_valid",
            ),
        ]

    def __str__(self):
        return f"{self.amount} {self.currency} - {self.get_status_display()}"

    def save(self, *args, **kwargs):
        self.currency = self.currency.strip().upper()
        super().save(*args, **kwargs)


class PaymentWebhookEvent(BaseMinModel):
    """A durable, idempotent inbox entry for each provider webhook event.

    Same design as your StripeWebhookEvent, just provider-tagged so bKash
    (or anything else) lands in the same table instead of a parallel one.
    """

    provider = models.CharField(max_length=20, choices=PaymentProvider.choices, db_index=True)
    provider_event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=255, db_index=True)
    api_version = models.CharField(max_length=40, blank=True)
    livemode = models.BooleanField(default=False, db_index=True)
    payload = models.JSONField(default=dict)
    processing_status = models.CharField(
        max_length=20, choices=WebhookProcessingStatus.choices,
        default=WebhookProcessingStatus.RECEIVED, db_index=True,
    )
    payment = models.ForeignKey(
        Payment, null=True, blank=True, on_delete=models.SET_NULL, related_name="webhook_events"
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    processed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["processing_status", "created_at"], name="sub_webhook_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_event_id"], name="sub_webhook_provider_event_unique"
            ),
        ]

    def __str__(self):
        return f"{self.provider}:{self.event_type} ({self.provider_event_id})"
