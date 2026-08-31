from django.contrib import admin
from django.utils.html import format_html

from lead_capture.models import Lead, LeadCaptureConfig, LeadNote


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
                    "intro_message",
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
        "lead_score",
        "location_display",
        "source",
        "created_at",
    )
    list_filter = ("status", "source", "detected_country_code", "created_at", "chatbot")
    search_fields = (
        "collected_fields__name",
        "collected_fields__email",
        "collected_fields__phone",
        "chatbot__chatbot_name",
        "chatbot__name",
        "initial_ip_address",
        "last_ip_address",
    )
    readonly_fields = (
        "initial_ip_address",
        "last_ip_address",
        "detected_country_code",
        "detected_city",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    inlines = [LeadNoteInline]
    fieldsets = (
        (
            "Overview",
            {
                "fields": (
                    "chatbot",
                    "status",
                    "lead_score",
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
            "Network & Geo Info",
            {
                "classes": ("collapse",),
                "fields": (
                    ("initial_ip_address", "last_ip_address"),
                    ("detected_city", "detected_country_code"),
                ),
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

    @admin.display(description="Location")
    def location_display(self, obj):
        parts = [p for p in (obj.detected_city, obj.detected_country_code) if p]
        return ", ".join(parts) or "—"


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