from django.contrib import admin

from chatbot.models import Chatbot, ChatbotUser


@admin.register(Chatbot)
class ChatbotAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "workspace__name")
    autocomplete_fields = ("workspace",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ChatbotUser)
class ChatbotUserAdmin(admin.ModelAdmin):
    list_display = ("chatbot", "user", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "created_at")
    search_fields = ("chatbot__name", "user__email", "user__name")
    autocomplete_fields = ("chatbot", "user")
    readonly_fields = ("id", "created_at", "updated_at")
