from rest_framework import serializers

from subscription.choices import BillingInterval, PaymentProvider
from subscription.models import ChatbotSubscription, Payment, PlanPrice, SubscriptionPlan


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


class SubscriptionCancellationSerializer(serializers.Serializer):
    cancel_at_period_end = serializers.BooleanField(default=True)


class ChatbotSubscriptionClientSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)
    plan_price_id = serializers.UUIDField(read_only=True)
    selected_by_id = serializers.UUIDField(read_only=True, allow_null=True)

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
            "cancel_at_period_end",
            "canceled_at",
            "ended_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


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
