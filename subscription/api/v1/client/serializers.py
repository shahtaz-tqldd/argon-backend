from rest_framework import serializers

from coupon.api.v1.client.serializers import CouponRedemptionClientSerializer
from coupon.models import Coupon
from subscription.choices import BillingInterval, PaymentProvider, PlanFeature
from subscription.models import (
    BillingPaymentMethod,
    ChatbotSubscription,
    EnterprisePlanRequest,
    Payment,
    PlanPrice,
    SubscriptionPlan,
)


class SubscriptionPlanQuerySerializer(serializers.Serializer):
    plan = serializers.SlugField()


class SubscriptionChatbotQuerySerializer(serializers.Serializer):
    chatbot = serializers.SlugField()


class PlanPriceClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanPrice
        fields = (
            "id",
            "provider",
            "billing_interval",
            "currency",
            "amount",
            "ai_message_overage_unit_price",
        )
        read_only_fields = fields


class SubscriptionPlanClientSerializer(serializers.ModelSerializer):
    prices = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPlan
        fields = (
            "id",
            "name",
            "slug",
            "plan_type",
            "ai_message_limit",
            "file_size_limit_mb",
            "knowledge_chunk_limit",
            "team_members_limit",
            "ai_message_overage_enabled",
            "features",
            "details_html",
            "is_free",
            "requires_sales_contact",
            "sort_order",
            "prices",
        )
        read_only_fields = fields

    def get_prices(self, obj):
        prices = getattr(obj, "available_prices", None)
        if prices is None:
            prices = obj.prices.filter(is_active=True).order_by(
                "billing_interval", "currency"
            )
        return PlanPriceClientSerializer(prices, many=True).data


class StripeCheckoutSerializer(serializers.Serializer):
    plan_price_id = serializers.PrimaryKeyRelatedField(
        source="plan_price",
        queryset=PlanPrice.objects.filter(
            provider=PaymentProvider.STRIPE,
            is_active=True,
            plan__is_active=True,
            plan__is_public=True,
        ).select_related("plan"),
    )
    coupon_code = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=50,
    )

    def validate_plan_price_id(self, plan_price):
        if plan_price.plan.is_free or plan_price.amount == 0:
            raise serializers.ValidationError(
                "Use the free-plan activation endpoint for this price."
            )
        if plan_price.plan.requires_sales_contact:
            raise serializers.ValidationError(
                "This plan requires contacting sales."
            )
        if plan_price.billing_interval not in {
            BillingInterval.MONTHLY,
            BillingInterval.ANNUAL,
        }:
            raise serializers.ValidationError(
                "Stripe checkout supports monthly or annual prices only."
            )
        return plan_price

    def validate_coupon_code(self, value):
        code = value.strip().upper()
        if not code:
            return ""
        if not Coupon.objects.filter(code__iexact=code).exists():
            raise serializers.ValidationError("No coupon exists for this code.")
        return code


class FreeSubscriptionSerializer(serializers.Serializer):
    plan_price_id = serializers.PrimaryKeyRelatedField(
        source="plan_price",
        queryset=PlanPrice.objects.filter(
            amount=0,
            is_active=True,
            plan__is_free=True,
            plan__is_active=True,
            plan__is_public=True,
            plan__requires_sales_contact=False,
        ).select_related("plan"),
    )


class EnterprisePlanRequestCreateSerializer(serializers.Serializer):
    requested_features = serializers.ListField(
        child=serializers.ChoiceField(choices=PlanFeature.choices),
        required=False,
        default=list,
        allow_empty=True,
    )
    requested_ai_message_limit = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )
    requested_file_size_limit_mb = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )
    requested_knowledge_chunk_limit = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )
    requested_daily_traffic = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
    )

    def validate_requested_features(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Features must be unique.")
        return value


class EnterprisePlanRequestSerializer(serializers.ModelSerializer):
    chatbot_slug = serializers.CharField(
        source="chatbot.slug", read_only=True
    )
    chatbot_name = serializers.CharField(
        source="chatbot.chatbot_name", read_only=True
    )
    workspace_name = serializers.CharField(
        source="chatbot.workspace.name", read_only=True
    )
    reviewed_by = serializers.SerializerMethodField()
    subscription_id = serializers.SerializerMethodField()

    class Meta:
        model = EnterprisePlanRequest
        fields = (
            "id",
            "chatbot_slug",
            "chatbot_name",
            "workspace_name",
            "status",
            "requested_features",
            "requested_ai_message_limit",
            "requested_file_size_limit_mb",
            "requested_knowledge_chunk_limit",
            "requested_daily_traffic",
            "notes",
            "approved_features",
            "approved_ai_message_limit",
            "approved_file_size_limit_mb",
            "approved_knowledge_chunk_limit",
            "expires_at",
            "subscription_id",
            "reviewed_by",
            "reviewed_at",
            "review_notes",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_reviewed_by(self, obj):
        return obj.reviewed_by.email if obj.reviewed_by else None

    def get_subscription_id(self, obj):
        return str(obj.subscription_id) if obj.subscription_id else None


class SubscriptionCancellationSerializer(serializers.Serializer):
    cancel_at_period_end = serializers.BooleanField(default=True)


class AutoRenewalSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()


class BillingPaymentMethodClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingPaymentMethod
        fields = (
            "id",
            "provider",
            "provider_payment_method_id",
            "card_brand",
            "card_last4",
            "card_exp_month",
            "card_exp_year",
            "is_default",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class DefaultPaymentMethodSerializer(serializers.Serializer):
    payment_method_id = serializers.CharField(max_length=255, trim_whitespace=True)


class ChatbotSubscriptionClientSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)
    plan_price_id = serializers.UUIDField(read_only=True)
    selected_by_id = serializers.UUIDField(read_only=True, allow_null=True)
    pending_coupon = serializers.SerializerMethodField()
    coupon_redemption = serializers.SerializerMethodField()
    default_payment_method = serializers.SerializerMethodField()
    auto_renewal_enabled = serializers.SerializerMethodField()

    class Meta:
        model = ChatbotSubscription
        fields = (
            "id",
            "chatbot_id",
            "plan_price_id",
            "selected_by_id",
            "snapshot",
            "provider",
            "renewal_mode",
            "status",
            "started_at",
            "current_period_start",
            "current_period_end",
            "next_billing_at",
            "cancel_at_period_end",
            "auto_renewal_enabled",
            "canceled_at",
            "ended_at",
            "pending_coupon",
            "coupon_redemption",
            "default_payment_method",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_pending_coupon(self, obj):
        return (obj.provider_metadata or {}).get("pending_coupon") or None

    def get_coupon_redemption(self, obj):
        redemption = (
            obj.coupon_redemptions.select_related("coupon", "coupon__discount")
            .filter(is_active=True)
            .order_by("-redeemed_at")
            .first()
        )
        if redemption is None:
            return None
        return CouponRedemptionClientSerializer(redemption).data

    def get_default_payment_method(self, obj):
        payment_method = obj.chatbot.billing_payment_methods.filter(
            provider=obj.provider,
            is_active=True,
            is_default=True,
        ).first()
        if payment_method is None:
            return None
        return BillingPaymentMethodClientSerializer(payment_method).data

    def get_auto_renewal_enabled(self, obj):
        return bool(
            obj.provider == PaymentProvider.STRIPE
            and obj.provider_subscription_id
            and not obj.cancel_at_period_end
        )


class PaymentClientSerializer(serializers.ModelSerializer):
    invoice_url = serializers.SerializerMethodField()
    invoice_pdf = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = (
            "id",
            "subscription_id",
            "plan_price_id",
            "provider",
            "payment_type",
            "status",
            "billing_interval",
            "amount",
            "amount_refunded",
            "currency",
            "description",
            "provider_reference",
            "failure_code",
            "failure_message",
            "paid_at",
            "refunded_at",
            "invoice_url",
            "invoice_pdf",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_invoice_url(self, obj):
        return (obj.provider_metadata or {}).get("hosted_invoice_url", "")

    def get_invoice_pdf(self, obj):
        return (obj.provider_metadata or {}).get("invoice_pdf", "")
