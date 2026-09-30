from django.db import models as db_models
from rest_framework import serializers

from coupon.choices import DiscountDuration, DiscountType
from coupon.models import Coupon, CouponRedemption, Discount
from subscription.models import SubscriptionPlan


class DiscountSerializer(serializers.ModelSerializer):
    """Create, update, and represent a discount definition."""

    class Meta:
        model = Discount
        fields = (
            "id",
            "name",
            "discount_type",
            "value",
            "currency",
            "duration",
            "duration_in_billing_cycles",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance
        discount_type = attrs.get(
            "discount_type", getattr(instance, "discount_type", None)
        )
        value = attrs.get("value", getattr(instance, "value", None))
        duration = attrs.get(
            "duration", getattr(instance, "duration", DiscountDuration.ONCE)
        )
        cycles = attrs.get(
            "duration_in_billing_cycles",
            getattr(instance, "duration_in_billing_cycles", None),
        )
        currency = (
            attrs.get("currency", getattr(instance, "currency", "")) or ""
        ).strip().upper()

        if value is not None and value <= 0:
            raise serializers.ValidationError(
                {"value": "The discount value must be greater than zero."}
            )
        if discount_type == DiscountType.PERCENTAGE:
            if value is not None and value > 100:
                raise serializers.ValidationError(
                    {"value": "A percentage discount cannot exceed 100%."}
                )
            if currency:
                raise serializers.ValidationError(
                    {"currency": "Percentage discounts do not use a currency."}
                )
        elif not currency:
            raise serializers.ValidationError(
                {"currency": "A fixed-amount discount requires a currency."}
            )

        if duration == DiscountDuration.REPEATING:
            if not cycles:
                raise serializers.ValidationError(
                    {
                        "duration_in_billing_cycles": (
                            "A repeating discount requires at least one "
                            "billing cycle."
                        )
                    }
                )
        elif cycles is not None:
            raise serializers.ValidationError(
                {
                    "duration_in_billing_cycles": (
                        "Only repeating discounts use a billing-cycle count."
                    )
                }
            )

        if "currency" in attrs:
            attrs["currency"] = currency
        return attrs


class CouponSerializer(serializers.ModelSerializer):
    """Create, update, and represent an administrator-managed coupon."""

    discount = DiscountSerializer(read_only=True)
    discount_data = DiscountSerializer(write_only=True, required=False)
    discount_id = serializers.PrimaryKeyRelatedField(
        source="discount_ref",
        queryset=Discount.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    eligible_plans = serializers.PrimaryKeyRelatedField(
        many=True,
        required=False,
        queryset=SubscriptionPlan.objects.filter(is_active=True),
    )
    redemptions_count = serializers.SerializerMethodField()
    active_redemptions_count = serializers.SerializerMethodField()
    last_redeemed_at = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = (
            "id",
            "code",
            "name",
            "discount",
            "discount_data",
            "discount_id",
            "is_active",
            "valid_from",
            "valid_until",
            "max_redemptions",
            "max_redemptions_per_user",
            "minimum_purchase_amount",
            "minimum_purchase_currency",
            "first_payment_only",
            "applies_to_all_plans",
            "eligible_plans",
            "metadata",
            "redemptions_count",
            "active_redemptions_count",
            "last_redeemed_at",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        )

    def get_redemptions_count(self, obj):
        if hasattr(obj, "redemptions_count"):
            return obj.redemptions_count
        return obj.redemptions.count()

    def get_active_redemptions_count(self, obj):
        if hasattr(obj, "active_redemptions_count"):
            return obj.active_redemptions_count
        return obj.redemptions.filter(is_active=True).count()

    def get_last_redeemed_at(self, obj):
        if hasattr(obj, "last_redeemed_at"):
            return obj.last_redeemed_at
        return obj.redemptions.aggregate(
            last=db_models.Max("redeemed_at")
        )["last"]

    def validate_code(self, value):
        code = value.strip().upper()
        if not code:
            raise serializers.ValidationError("The coupon code is required.")
        queryset = Coupon.objects.filter(code__iexact=code)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(
                "A coupon with this code already exists."
            )
        return code

    def validate(self, attrs):
        attrs = super().validate(attrs)
        discount_data = attrs.pop("discount_data", None)
        discount_ref = attrs.pop("discount_ref", None)

        instance = self.instance
        creating = instance is None

        if creating and discount_data is None and discount_ref is None:
            raise serializers.ValidationError(
                {
                    "discount": (
                        "Provide either discount_data or discount_id."
                    )
                }
            )
        if discount_data is not None and discount_ref is not None:
            raise serializers.ValidationError(
                {
                    "discount": (
                        "Provide either discount_data or discount_id, not both."
                    )
                }
            )

        if (
            not creating
            and discount_ref is not None
            and discount_ref != instance.discount
            and instance.redemptions.exists()
        ):
            raise serializers.ValidationError(
                {
                    "discount_id": (
                        "This coupon has redemptions; create a new coupon "
                        "with a different discount instead."
                    )
                }
            )

        valid_from = attrs.get(
            "valid_from", getattr(instance, "valid_from", None)
        )
        valid_until = attrs.get(
            "valid_until", getattr(instance, "valid_until", None)
        )
        if (
            valid_until is not None
            and valid_from is not None
            and valid_until <= valid_from
        ):
            raise serializers.ValidationError(
                {"valid_until": "The coupon must expire after its start time."}
            )

        min_amount = attrs.get(
            "minimum_purchase_amount",
            getattr(instance, "minimum_purchase_amount", None),
        )
        min_currency = (
            attrs.get(
                "minimum_purchase_currency",
                getattr(instance, "minimum_purchase_currency", ""),
            )
            or ""
        ).strip().upper()

        if min_amount is not None and not min_currency:
            raise serializers.ValidationError(
                {
                    "minimum_purchase_currency": (
                        "A minimum purchase amount requires a currency."
                    )
                }
            )
        if min_amount is None and min_currency:
            raise serializers.ValidationError(
                {
                    "minimum_purchase_currency": (
                        "Set a minimum purchase amount before setting its "
                        "currency."
                    )
                }
            )
        if "minimum_purchase_currency" in attrs:
            attrs["minimum_purchase_currency"] = min_currency

        applies_to_all = attrs.get(
            "applies_to_all_plans",
            getattr(instance, "applies_to_all_plans", True),
        )
        plans = attrs.get("eligible_plans", None)
        if not applies_to_all:
            if creating and not plans:
                raise serializers.ValidationError(
                    {
                        "eligible_plans": (
                            "Select at least one eligible plan or allow all "
                            "plans."
                        )
                    }
                )
            if not creating and plans is not None and not plans:
                raise serializers.ValidationError(
                    {
                        "eligible_plans": (
                            "Select at least one eligible plan or allow all "
                            "plans."
                        )
                    }
                )

        attrs["_discount_data"] = discount_data
        attrs["_discount_ref"] = discount_ref
        return attrs

    def create(self, validated_data):
        eligible_plans = validated_data.pop("eligible_plans", [])
        discount_data = validated_data.pop("_discount_data", None)
        discount_ref = validated_data.pop("_discount_ref", None)

        if discount_data is not None:
            discount_data.setdefault(
                "created_by", validated_data.get("created_by")
            )
            discount_data.setdefault(
                "updated_by", validated_data.get("updated_by")
            )
            discount = Discount.objects.create(**discount_data)
        else:
            discount = discount_ref

        coupon = Coupon.objects.create(discount=discount, **validated_data)
        if eligible_plans:
            coupon.eligible_plans.set(eligible_plans)
        return coupon

    def update(self, instance, validated_data):
        eligible_plans = validated_data.pop("eligible_plans", None)
        discount_data = validated_data.pop("_discount_data", None)
        discount_ref = validated_data.pop("_discount_ref", None)

        if discount_data is not None:
            discount_serializer = DiscountSerializer(
                instance.discount,
                data=discount_data,
                partial=True,
            )
            discount_serializer.is_valid(raise_exception=True)
            discount_serializer.save(
                updated_by=validated_data.get("updated_by")
            )

        if discount_ref is not None:
            instance.discount = discount_ref

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if eligible_plans is not None:
            instance.eligible_plans.set(eligible_plans)
        return instance


class CouponRedemptionAdminSerializer(serializers.ModelSerializer):
    """A redemption record for admin usage reporting."""

    coupon_id = serializers.UUIDField(read_only=True)
    subscription_id = serializers.UUIDField(read_only=True)
    payment_id = serializers.UUIDField(read_only=True)
    user_email = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    payment_provider_reference = serializers.SerializerMethodField()

    class Meta:
        model = CouponRedemption
        fields = (
            "id",
            "coupon_id",
            "subscription_id",
            "payment_id",
            "user_id",
            "user_email",
            "coupon_code_snapshot",
            "discount_name_snapshot",
            "discount_type_snapshot",
            "discount_value_snapshot",
            "discount_currency_snapshot",
            "discount_duration_snapshot",
            "duration_in_billing_cycles_snapshot",
            "billing_cycles_remaining",
            "original_amount",
            "discount_amount",
            "final_amount",
            "currency",
            "is_active",
            "payment_status",
            "payment_provider_reference",
            "redeemed_at",
            "expires_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_user_email(self, obj):
        return obj.user.email if obj.user is not None else None

    def get_payment_status(self, obj):
        return obj.payment.status if obj.payment is not None else None

    def get_payment_provider_reference(self, obj):
        return (
            obj.payment.provider_reference
            if obj.payment is not None
            else None
        )
