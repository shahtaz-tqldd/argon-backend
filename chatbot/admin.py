from django.contrib import admin

from chatbot.models import (
    Chatbot,
    ChatbotAllowedOrigin,
    ChatbotInvitation,
    ChatbotUser,
    ChatbotWidgetSettings,
)


@admin.register(Chatbot)
class ChatbotAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "workspace__name")
    autocomplete_fields = ("workspace",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ChatbotWidgetSettings)
class ChatbotWidgetSettingsAdmin(admin.ModelAdmin):
    list_display = ("chatbot", "is_enabled", "theme", "launcher_position")
    list_filter = ("is_enabled", "theme", "launcher_position")
    search_fields = ("chatbot__name", "chatbot__workspace__name", "public_key")
    autocomplete_fields = ("chatbot",)
    readonly_fields = ("id", "public_key", "created_at", "updated_at")


@admin.register(ChatbotAllowedOrigin)
class ChatbotAllowedOriginAdmin(admin.ModelAdmin):
    list_display = ("origin", "chatbot", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("origin", "chatbot__name")
    autocomplete_fields = ("chatbot",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ChatbotUser)
class ChatbotUserAdmin(admin.ModelAdmin):
    list_display = ("chatbot", "user", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "created_at")
    search_fields = ("chatbot__name", "user__email", "user__name")
    autocomplete_fields = ("chatbot", "user")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ChatbotInvitation)
class ChatbotInvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "chatbot", "expires_at", "accepted_at", "created_at")
    list_filter = ("accepted_at", "expires_at", "created_at")
    search_fields = ("email", "chatbot__name", "chatbot__workspace__name")
    autocomplete_fields = ("chatbot",)
    readonly_fields = ("id", "token_hash", "created_at", "updated_at")
