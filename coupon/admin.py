from django.contrib import admin
from .models import Discount, Coupon, CouponRedemption


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "discount_type",
        "value",
        "currency",
        "duration",
        "is_active",
    )
    list_filter = ("discount_type", "duration", "is_active")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "discount",
        "is_active",
        "valid_from",
        "valid_until",
    )
    list_filter = (
        "is_active",
        "first_payment_only",
        "applies_to_all_plans",
        "valid_from",
        "valid_until",
    )
    search_fields = ("code", "name", "discount__name")
    raw_id_fields = ("discount",)
    filter_horizontal = ("eligible_plans",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = (
        "coupon_code_snapshot",
        "user",
        "subscription",
        "final_amount",
        "currency",
        "is_active",
        "redeemed_at",
    )
    list_filter = (
        "is_active",
        "discount_type_snapshot",
        "discount_duration_snapshot",
        "redeemed_at",
    )
    search_fields = (
        "coupon__code",
        "coupon_code_snapshot",
        "user__email",
        "user__username",
    )
    raw_id_fields = ("coupon", "subscription", "payment", "user")
    
    # Snapshot fields are editable=False in the model, so we must add them 
    # to readonly_fields to display them in the admin form.
    readonly_fields = (
        "coupon_code_snapshot",
        "discount_name_snapshot",
        "discount_type_snapshot",
        "discount_value_snapshot",
        "discount_currency_snapshot",
        "discount_duration_snapshot",
        "duration_in_billing_cycles_snapshot",
        "created_at", 
        "updated_at",
    )
    
    fieldsets = (
        (None, {
            "fields": (
                "coupon",
                "subscription",
                "payment",
                "user",
                "is_active",
            )
        }),
        ("Amounts & Dates", {
            "fields": (
                "original_amount",
                "discount_amount",
                "final_amount",
                "currency",
                "billing_cycles_remaining",
                "redeemed_at",
                "expires_at",
            )
        }),
        ("Discount Snapshot", {
            "fields": (
                "coupon_code_snapshot",
                "discount_name_snapshot",
                "discount_type_snapshot",
                "discount_value_snapshot",
                "discount_currency_snapshot",
                "discount_duration_snapshot",
                "duration_in_billing_cycles_snapshot",
            ),
            "classes": ("collapse",),
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )