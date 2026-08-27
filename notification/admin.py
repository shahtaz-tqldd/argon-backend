from django.contrib import admin

from notification.models import Notification, NotificationRead


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "recipient_type",
        "notification_type",
        "recipient",
        "workspace",
        "chatbot",
        "target_id",
        "created_at",
    )
    list_filter = ("recipient_type", "notification_type", "created_at")
    search_fields = (
        "title",
        "message",
        "recipient__email",
        "recipient__name",
        "workspace__name",
        "chatbot__chatbot_name",
    )
    autocomplete_fields = ("recipient", "workspace", "chatbot")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(NotificationRead)
class NotificationReadAdmin(admin.ModelAdmin):
    list_display = ("notification", "user", "read_at")
    search_fields = ("notification__title", "user__email", "user__name")
    readonly_fields = ("id", "read_at", "created_at", "updated_at")
