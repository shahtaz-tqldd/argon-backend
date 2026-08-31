from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from chat_session.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatSession,
    ChatSessionTakeover,
)


class ChatMessageAttachmentInline(admin.TabularInline):
    model = ChatMessageAttachment
    extra = 0
    fields = (
        "attachment_type",
        "file_name",
        "file_url_link",
        "mime_type",
        "file_size",
        "sort_order",
    )
    readonly_fields = ("file_url_link",)

    @admin.display(description="File Link")
    def file_url_link(self, obj):
        if not obj.file_url:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">View Resource</a>',
            obj.file_url,
        )


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "created_at",
        "sender_type",
        "sender",
        "content_preview",
        "status",
    )
    readonly_fields = (
        "created_at",
        "sender_type",
        "sender",
        "content_preview",
        "status",
    )

    @admin.display(description="Message")
    def content_preview(self, obj):
        if not obj.content:
            att_count = obj.attachments.count() if obj.pk else 0
            return format_html("<em>[{} attachment(s)]</em>", att_count)
        return (obj.content[:75] + "...") if len(obj.content) > 75 else obj.content


class ChatSessionTakeoverInline(admin.TabularInline):
    model = ChatSessionTakeover
    extra = 0
    show_change_link = True
    fields = (
        "agent",
        "created_at",
        "released_at",
        "release_reason",
        "is_active_badge",
    )
    readonly_fields = ("created_at", "is_active_badge")
    autocomplete_fields = ("agent",)

    @admin.display(description="Status")
    def is_active_badge(self, obj):
        if not obj.pk:
            return "—"
        if obj.is_active:
            return format_html(
                '<span style="color: #28a745; font-weight: bold;">Active</span>'
            )
        return format_html('<span style="color: #6c757d;">Released</span>')


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id_short",
        "visitor_or_lead",
        "chatbot",
        "channel",
        "status",
        "assigned_to",
        "ai_enabled",
        "last_activity_at",
    )
    list_filter = (
        "status",
        "channel",
        "ai_enabled",
        "chatbot",
        ("assigned_to", admin.EmptyFieldListFilter),
        "last_activity_at",
    )
    search_fields = (
        "id",
        "visitor_id",
        "external_thread_id",
        "lead__collected_fields__name",
        "lead__collected_fields__email",
        "lead__collected_fields__phone",
        "chatbot__chatbot_name",
        "chatbot__name",
        "assigned_to__user__username",
        "assigned_to__user__email",
    )
    readonly_fields = ("last_activity_at", "created_at", "updated_at")
    date_hierarchy = "last_activity_at"
    autocomplete_fields = ("lead", "assigned_to")
    inlines = [ChatSessionTakeoverInline, ChatMessageInline]

    fieldsets = (
        (
            "Session Identifiers",
            {
                "fields": (
                    "chatbot",
                    "channel",
                    "status",
                    "lead",
                    "visitor_id",
                    "external_thread_id",
                ),
            },
        ),
        (
            "Routing & AI Control",
            {
                "fields": (
                    "assigned_to",
                    "ai_enabled",
                ),
            },
        ),
        (
            "Session Lifetime",
            {
                "fields": (
                    "last_activity_at",
                    "ended_at",
                ),
            },
        ),
        (
            "Context & Metadata",
            {
                "classes": ("collapse",),
                "fields": (
                    "user_metadata",
                    "metadata",
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

    @admin.display(description="Session")
    def id_short(self, obj):
        return str(obj.pk)[:8]

    @admin.display(description="Visitor / Lead")
    def visitor_or_lead(self, obj):
        if obj.lead:
            return format_html("<strong>{}</strong>", str(obj.lead))
        if obj.visitor_id:
            truncated = (
                (obj.visitor_id[:16] + "...")
                if len(obj.visitor_id) > 16
                else obj.visitor_id
            )
            return format_html("<span style='color: #6c757d;'>{}</span>", truncated)
        return mark_safe("<span style='color: #adb5bd;'>Anonymous</span>")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = (
        "chat_session_link",
        "sender_badge",
        "sender",
        "message_preview",
        "status",
        "created_at",
    )
    list_filter = (
        "sender_type",
        "status",
        "created_at",
        "chat_session__channel",
    )
    search_fields = (
        "content",
        "external_id",
        "chat_session__id",
        "sender__user__username",
        "sender__user__email",
    )
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("chat_session", "sender")
    inlines = [ChatMessageAttachmentInline]

    fieldsets = (
        (
            "Message Context",
            {
                "fields": (
                    "chat_session",
                    "sender_type",
                    "sender",
                    "status",
                ),
            },
        ),
        (
            "Content",
            {
                "fields": (
                    "content",
                    "external_id",
                ),
            },
        ),
        (
            "Metadata",
            {
                "classes": ("collapse",),
                "fields": ("metadata",),
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

    @admin.display(description="Session")
    def chat_session_link(self, obj):
        return str(obj.chat_session_id)[:8]

    @admin.display(description="Sender Type")
    def sender_badge(self, obj):
        colors = {
            "visitor": "#17a2b8",
            "ai": "#6f42c1",
            "agent": "#28a745",
            "system": "#6c757d",
        }
        color = colors.get(obj.sender_type, "#000000")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_sender_type_display(),
        )

    @admin.display(description="Content")
    def message_preview(self, obj):
        if not obj.content:
            att_count = obj.attachments.count() if obj.pk else 0
            return format_html("<em>[{} attachment(s)]</em>", att_count)
        return (obj.content[:90] + "...") if len(obj.content) > 90 else obj.content


@admin.register(ChatMessageAttachment)
class ChatMessageAttachmentAdmin(admin.ModelAdmin):
    list_display = (
        "file_name_display",
        "chat_message",
        "attachment_type",
        "mime_type",
        "file_size",
        "created_at",
    )
    list_filter = ("attachment_type", "created_at")
    search_fields = (
        "file_name",
        "file_url",
        "mime_type",
        "chat_message__id",
    )
    readonly_fields = ("file_url_link", "created_at", "updated_at")
    autocomplete_fields = ("chat_message",)

    @admin.display(description="Name")
    def file_name_display(self, obj):
        return obj.file_name or "Unnamed File"

    @admin.display(description="Resource Link")
    def file_url_link(self, obj):
        if not obj.file_url:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            obj.file_url,
            obj.file_url,
        )


@admin.register(ChatSessionTakeover)
class ChatSessionTakeoverAdmin(admin.ModelAdmin):
    list_display = (
        "chat_session",
        "agent",
        "is_active_badge",
        "release_reason",
        "created_at",
        "released_at",
        "reopened_at",
    )
    list_filter = (
        "release_reason",
        ("released_at", admin.EmptyFieldListFilter),
        ("reopened_at", admin.EmptyFieldListFilter),
        "created_at",
    )
    search_fields = (
        "chat_session__id",
        "agent__user__username",
        "agent__user__email",
        "resolution_note",
    )
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("chat_session", "agent", "reopened_by")

    fieldsets = (
        (
            "Takeover Assignment",
            {
                "fields": ("chat_session", "agent"),
            },
        ),
        (
            "Handoff / Resolution Details",
            {
                "fields": (
                    "released_at",
                    "release_reason",
                    "resolution_note",
                ),
            },
        ),
        (
            "Reopening State",
            {
                "classes": ("collapse",),
                "fields": (
                    "reopened_at",
                    "reopened_by",
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

    @admin.display(description="Status")
    def is_active_badge(self, obj):
        if obj.is_active:
            return format_html(
                '<span style="color: #28a745; font-weight: bold;">Active</span>'
            )
        return format_html('<span style="color: #6c757d;">Released</span>')
    