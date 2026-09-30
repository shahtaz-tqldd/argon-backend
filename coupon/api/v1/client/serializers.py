from rest_framework import serializers

from coupon.models import CouponRedemption
from subscription.models import PlanPrice


class CouponRedemptionClientSerializer(serializers.ModelSerializer):
    """An applied coupon with its immutable discount snapshot."""

    coupon_id = serializers.UUIDField(read_only=True)
    subscription_id = serializers.UUIDField(read_only=True)
    payment_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = CouponRedemption
        fields = (
            "id",
            "coupon_id",
            "subscription_id",
            "payment_id",
            "coupon_code_snapshot",
            "discount_name_snapshot",
            "discount_type_snapshot",
            "discount_value_snapshot",
            "discount_currency_snapshot",
            "discount_duration_snapshot",
            "billing_cycles_remaining",
            "original_amount",
            "discount_amount",
            "final_amount",
            "currency",
            "is_active",
            "redeemed_at",
            "expires_at",
        )
        read_only_fields = fields


class CouponApplySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
    plan_price_id = serializers.PrimaryKeyRelatedField(
        source="plan_price",
        required=False,
        allow_null=True,
        queryset=PlanPrice.objects.filter(
            is_active=True,
            plan__is_active=True,
            plan__is_public=True,
        ).select_related("plan"),
    )
