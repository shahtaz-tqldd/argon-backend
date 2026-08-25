from django.contrib import admin

from subscription.models import (
    ChatbotSubscription,
    Payment,
    PaymentWebhookEvent,
    PlanPrice,
    SubscriptionPlan,
)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "plan_type",
        "is_free",
        "is_public",
        "is_active",
        "sort_order",
        "updated_at",
    )
    list_filter = ("plan_type", "is_free", "is_public", "is_active")
    search_fields = ("name", "slug", "details_html")
    readonly_fields = ("id", "slug", "created_at", "updated_at")
    ordering = ("sort_order", "name")


@admin.register(PlanPrice)
class PlanPriceAdmin(admin.ModelAdmin):
    list_display = (
        "plan",
        "provider",
        "billing_interval",
        "amount",
        "currency",
        "is_active",
        "updated_at",
    )
    list_filter = ("provider", "billing_interval", "currency", "is_active")
    search_fields = (
        "plan__name",
        "plan__slug",
        "provider_price_id",
        "provider_overage_price_id",
    )
    autocomplete_fields = ("plan",)
    readonly_fields = ("id", "created_at", "updated_at")
    list_select_related = ("plan",)


@admin.register(ChatbotSubscription)
class ChatbotSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "chatbot",
        "plan_price",
        "provider",
        "status",
        "current_period_end",
        "next_billing_at",
        "cancel_at_period_end",
        "created_at",
    )
    list_filter = (
        "provider",
        "renewal_mode",
        "status",
        "cancel_at_period_end",
    )
    search_fields = (
        "chatbot__name",
        "chatbot__workspace__name",
        "plan_price__plan__name",
        "selected_by__email",
        "provider_customer_id",
        "provider_subscription_id",
    )
    autocomplete_fields = ("chatbot", "plan_price", "selected_by")
    readonly_fields = ("id", "provider", "snapshot", "created_at", "updated_at")
    list_select_related = ("chatbot", "plan_price", "plan_price__plan")

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly_fields.append("plan_price")
        return readonly_fields


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "provider_reference",
        "provider",
        "payment_type",
        "status",
        "amount",
        "currency",
        "subscription",
        "user",
        "paid_at",
        "created_at",
    )
    list_filter = (
        "provider",
        "payment_type",
        "status",
        "billing_interval",
        "currency",
        "created_at",
    )
    search_fields = (
        "provider_reference",
        "provider_customer_id",
        "idempotency_key",
        "description",
        "user__email",
        "subscription__chatbot__name",
        "plan_price__plan__name",
    )
    autocomplete_fields = ("subscription", "plan_price", "user")
    readonly_fields = ("id", "created_at", "updated_at")
    list_select_related = (
        "subscription",
        "subscription__chatbot",
        "plan_price",
        "user",
    )


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "provider_event_id",
        "provider",
        "event_type",
        "processing_status",
        "livemode",
        "attempts",
        "payment",
        "processed_at",
        "created_at",
    )
    list_filter = (
        "provider",
        "processing_status",
        "livemode",
        "created_at",
    )
    search_fields = (
        "provider_event_id",
        "event_type",
        "last_error",
        "payment__provider_reference",
    )
    autocomplete_fields = ("payment",)
    readonly_fields = ("id", "payload", "created_at", "updated_at")
    list_select_related = ("payment",)
