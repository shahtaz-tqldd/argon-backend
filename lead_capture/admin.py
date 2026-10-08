from django.contrib import admin
from django.utils.html import format_html

from lead_capture.models import (
    Lead,
    LeadAIInsight,
    LeadCaptureConfig,
    LeadNote,
    LeadSignal,
)


class LeadSignalInline(admin.TabularInline):
    model = LeadSignal
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ("score", "intent", "message", "created_at")
    readonly_fields = ("score", "intent", "message", "created_at")


class LeadNoteInline(admin.TabularInline):
    model = LeadNote
    extra = 1
    fields = ("author", "content", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("author",)


@admin.register(LeadCaptureConfig)
class LeadCaptureConfigAdmin(admin.ModelAdmin):
    list_display = (
        "chatbot",
        "is_enabled",
        "auto_collect",
        "require_consent",
        "updated_at",
    )
    list_filter = ("is_enabled", "auto_collect", "require_consent")
    search_fields = ("chatbot__chatbot_name", "chatbot__name", "chatbot__id")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": ("chatbot", "is_enabled", "auto_collect"),
            },
        ),
        (
            "Consent & Messaging",
            {
                "fields": (
                    "require_consent",
                    "consent_message",
                ),
            },
        ),
        (
            "Field Schema",
            {
                "classes": ("collapse",),
                "fields": ("collectable_fields",),
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "lead_identity",
        "chatbot",
        "status",
        "avg_score",
        "source",
        "created_at",
    )
    list_filter = ("status", "source", "created_at", "chatbot")
    search_fields = (
        "collected_fields__name",
        "collected_fields__email",
        "collected_fields__phone",
        "chatbot__chatbot_name",
        "chatbot__name",
    )
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    inlines = [LeadSignalInline, LeadNoteInline]
    fieldsets = (
        (
            "Overview",
            {
                "fields": (
                    "chatbot",
                    "status",
                    "avg_score",
                    "source",
                ),
            },
        ),
        (
            "Collected Lead Details",
            {
                "fields": ("collected_fields",),
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    @admin.display(description="Lead")
    def lead_identity(self, obj):
        data = obj.collected_fields if isinstance(obj.collected_fields, dict) else {}
        name = data.get("name")
        email = data.get("email")
        phone = data.get("phone")

        primary = name or email or phone or f"Lead #{obj.pk}"
        secondary = email if name and email else (phone if name else None)

        if secondary:
            return format_html(
                "<strong>{}</strong><br><small style='color: gray;'>{}</small>",
                primary,
                secondary,
            )
        return primary


@admin.register(LeadSignal)
class LeadSignalAdmin(admin.ModelAdmin):
    list_display = ("lead", "score", "intent", "message", "created_at")
    list_filter = ("score", "created_at")
    search_fields = (
        "intent",
        "lead__collected_fields__name",
        "lead__collected_fields__email",
    )
    autocomplete_fields = ("lead",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(LeadAIInsight)
class LeadAIInsightAdmin(admin.ModelAdmin):
    list_display = (
        "chatbot",
        "week_start",
        "week_end",
        "visitor_message_count",
        "session_count",
        "created_at",
    )
    list_filter = ("week_start",)
    search_fields = ("chatbot__chatbot_name", "chatbot__slug")
    readonly_fields = (
        "chatbot",
        "week_start",
        "week_end",
        "session_count",
        "visitor_message_count",
        "summary",
        "topics",
        "frequently_asked_questions",
        "common_intents",
        "metadata",
        "created_at",
        "updated_at",
    )


@admin.register(LeadNote)
class LeadNoteAdmin(admin.ModelAdmin):
    list_display = ("lead", "author", "truncated_content", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "content",
        "author__user__username",
        "author__user__email",
        "lead__collected_fields__name",
        "lead__collected_fields__email",
    )
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("lead", "author")

    @admin.display(description="Content")
    def truncated_content(self, obj):
        if len(obj.content) > 75:
            return f"{obj.content[:75]}..."
        return obj.content