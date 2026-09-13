from django.contrib import admin

from base.models import ArgonChatbotConfig


@admin.register(ArgonChatbotConfig)
class ArgonChatbotConfigAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "support_email",
        "is_vectorize_enabled",
        "notify_banner_enabled",
        "updated_at",
    )
    readonly_fields = ("updated_by", "created_at", "updated_at")
