from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from app.base.models import BaseMinModel
from chat_session.utils.choices import (
    ChatMessageAttachmentType,
    ChatMessageSenderType,
    ChatMessageStatus,
    ChatSessionChannel,
    ChatSessionStatus,
    ChatSessionTakeoverReleaseReason,
)
from chat_session.utils.validators import validate_json_object


class ChatSession(BaseMinModel):
    """A single conversation between a visitor and a chatbot."""
    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    channel = models.CharField(
        max_length=20,
        choices=ChatSessionChannel.choices,
        default=ChatSessionChannel.WEB_WIDGET,
    )
    status = models.CharField(
        max_length=24,
        choices=ChatSessionStatus.choices,
        default=ChatSessionStatus.ACTIVE,
        db_index=True,
    )

    lead = models.ForeignKey(
        "lead_capture.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_sessions",
        help_text=(
            "Set once the visitor is identified (e.g. via a lead-capture form "
            "or matched contact info). Null means the session is anonymous."
        ),
    )

    assigned_to = models.ForeignKey(
        "chatbot.ChatbotUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_chat_sessions",
        help_text=(
            "Denormalized pointer to the chatbot member currently responsible "
            "for this session. Kept in sync with the latest open "
            "ChatSessionTakeover row by the service layer."
        ),
    )

    visitor_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Anonymous fingerprint/cookie ID used to correlate sessions from "
            "the same unidentified visitor. Ignored once `lead` is set."
        ),
    )

    external_thread_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Channel-native conversation identifier — Messenger PSID, "
            "Instagram IGSID, WhatsApp phone number, or an API-side "
            "conversation key. Used to resume an existing session on "
            "inbound webhook events instead of creating a duplicate."
        ),
    )

    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    ai_enabled = models.BooleanField(default=True, verbose_name=_("AI Enabled"))

    # add ip address, detected address, name or email if leads are not active
    user_metadata = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
        help_text="Channel-specific session context stored as a JSON object.",
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
        help_text="Channel-specific session context stored as a JSON object.",
    )

    class Meta:
        ordering = ["-last_activity_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["chatbot", "status", "-last_activity_at"],
                name="chat_session_inbox_idx",
            ),
            models.Index(
                fields=["assigned_to", "status", "-last_activity_at"],
                name="chat_session_agent_idx",
            ),
            models.Index(
                fields=["chatbot", "visitor_id"],
                name="chat_session_visitor_idx",
            ),
            models.Index(
                fields=["lead"],
                name="chat_session_lead_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["chatbot", "channel", "external_thread_id"],
                condition=~Q(external_thread_id=""),
                name="unique_external_thread_per_channel",
            ),
        ]

    def clean(self):
        super().clean()
        if self.assigned_to_id:
            if not self.assigned_to.is_active:
                raise ValidationError(
                    {"assigned_to": "An inactive chatbot member cannot be assigned."}
                )
            if self.chatbot_id != self.assigned_to.chatbot_id:
                raise ValidationError(
                    {
                        "assigned_to": (
                            "The assigned member must belong to this session's "
                            "chatbot."
                        )
                    }
                )
        if self.lead_id and self.chatbot_id:
            if self.lead.chatbot_id != self.chatbot_id:
                raise ValidationError(
                    {"lead": "The lead must belong to this session's chatbot."}
                )

    def __str__(self):
        identity = self.lead_id or self.visitor_id or "anonymous visitor"
        return f"{identity} with {self.chatbot}"


class ChatMessage(BaseMinModel):
    """One ordered message in a chat session."""
    chat_session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender_type = models.CharField(
        max_length=12,
        choices=ChatMessageSenderType.choices,
    )
    sender = models.ForeignKey(
        "chatbot.ChatbotUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_messages",
        help_text="Set only when sender_type is agent.",
    )
    content = models.TextField(
        blank=True,
        default="",
        help_text="May be blank for attachment-only messages (see attachments).",
    )
    status = models.CharField(
        max_length=12,
        choices=ChatMessageStatus.choices,
        default=ChatMessageStatus.SENT,
        db_index=True,
    )
    external_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional channel message ID used for idempotent ingestion.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
        help_text="Delivery, AI, citation, or channel data stored as a JSON object.",
    )

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["chat_session", "created_at"],
                name="chat_message_timeline_idx",
            ),
            models.Index(
                fields=["chat_session", "status", "created_at"],
                name="chat_message_status_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(sender_type=ChatMessageSenderType.AGENT)
                    | Q(
                        sender_type__in=[
                            ChatMessageSenderType.VISITOR,
                            ChatMessageSenderType.AI,
                            ChatMessageSenderType.SYSTEM,
                        ],
                        sender__isnull=True,
                    )
                ),
                name="chat_message_sender_shape_valid",
            ),
            models.UniqueConstraint(
                fields=["chat_session", "external_id"],
                condition=~Q(external_id=""),
                name="unique_external_chat_message",
            ),
        ]

    def clean(self):
        super().clean()
        # content OR at least one attachment is required; enforced here
        # rather than a DB CheckConstraint because it spans two tables.
        has_content = bool(self.content and self.content.strip())
        has_attachments = self.pk and self.attachments.exists()
        if not has_content and not has_attachments:
            raise ValidationError(
                {"content": "Message must have content or at least one attachment."}
            )

        if self.sender_type == ChatMessageSenderType.AGENT:
            if not self.sender_id:
                raise ValidationError(
                    {"sender": "Agent messages require a chatbot member."}
                )
            if not self.sender.is_active:
                raise ValidationError(
                    {"sender": "An inactive chatbot member cannot send messages."}
                )
            if (
                self.chat_session_id
                and self.sender.chatbot_id != self.chat_session.chatbot_id
            ):
                raise ValidationError(
                    {
                        "sender": (
                            "The sender must belong to the chat session's chatbot."
                        )
                    }
                )
        elif self.sender_id:
            raise ValidationError(
                {"sender": "Only agent messages can have a chatbot member sender."}
            )

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            ChatSession.objects.filter(
                pk=self.chat_session_id,
                last_activity_at__lt=self.created_at,
            ).update(last_activity_at=self.created_at)

    def __str__(self):
        return f"{self.get_sender_type_display()} message in {self.chat_session_id}"


class ChatMessageAttachment(BaseMinModel):
    """A file or image attached to a chat message."""
    chat_message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    attachment_type = models.CharField(
        max_length=12,
        choices=ChatMessageAttachmentType.choices,
    )
    file_url = models.URLField(max_length=2048)
    file_name = models.CharField(max_length=255, blank=True, default="")
    mime_type = models.CharField(max_length=100, blank=True, default="")
    file_size = models.PositiveIntegerField(
        null=True, blank=True, help_text="Size in bytes."
    )
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(
        null=True, blank=True, help_text="For audio/video attachments."
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "created_at"]
        indexes = [
            models.Index(
                fields=["chat_message"],
                name="chat_attachment_message_idx",
            ),
        ]

    def __str__(self):
        return self.file_name or self.file_url


class ChatSessionTakeover(BaseMinModel):
    """
    One period of an agent being responsible for a session, from take-over to
    release. Release can be a handoff (REASSIGNED/MANUAL_RELEASE) or a
    resolution (RESOLVED/CLOSED). A session can only be resolved while it has
    an active (unreleased) takeover row — i.e. someone must own it to close it.
    If a resolved session is reopened, that's recorded on this same row;
    resolving it again requires a fresh takeover row.
    """

    chat_session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="takeovers",
    )
    agent = models.ForeignKey(
        "chatbot.ChatbotUser",
        on_delete=models.CASCADE,
        related_name="chat_session_takeovers",
    )

    # --- release (handoff or resolution) ---
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.CharField(
        max_length=20,
        choices=ChatSessionTakeoverReleaseReason.choices,
        blank=True,
        default="",
    )
    resolution_note = models.TextField(blank=True, default="")

    # --- reopen (only meaningful if release_reason was RESOLVED/CLOSED) ---
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        "chatbot.ChatbotUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_session_reopenings",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chat_session", "-created_at"],
                name="chat_takeover_session_idx",
            ),
            models.Index(
                fields=["agent", "-created_at"],
                name="chat_takeover_agent_idx",
            ),
        ]
        constraints = [
            # Only one open takeover per session at a time. This is what
            # forces "must take over before resolve" — resolving is just
            # releasing this row, and you can't release a row that doesn't
            # exist as the active one.
            models.UniqueConstraint(
                fields=["chat_session"],
                condition=Q(released_at__isnull=True),
                name="unique_active_takeover_per_session",
            ),
            # released_at and release_reason must be set together
            models.CheckConstraint(
                condition=(
                    Q(released_at__isnull=True, release_reason="")
                    | Q(released_at__isnull=False, release_reason__gt="")
                ),
                name="chat_takeover_release_fields_consistent",
            ),
            # reopened_* only valid on rows released as RESOLVED/CLOSED
            models.CheckConstraint(
                condition=(
                    Q(reopened_at__isnull=True)
                    | Q(
                        release_reason__in=[
                            ChatSessionTakeoverReleaseReason.RESOLVED,
                            ChatSessionTakeoverReleaseReason.CLOSED,
                        ]
                    )
                ),
                name="chat_takeover_reopen_requires_resolution",
            ),
        ]

    @property
    def is_active(self):
        return self.released_at is None

    @property
    def is_resolution(self):
        return self.release_reason in (
            ChatSessionTakeoverReleaseReason.RESOLVED,
            ChatSessionTakeoverReleaseReason.CLOSED,
        )

    def clean(self):
        super().clean()
        if self.agent_id and self.chat_session_id:
            if self.agent.chatbot_id != self.chat_session.chatbot_id:
                raise ValidationError(
                    {"agent": "The agent must belong to the session's chatbot."}
                )
        if self.released_at and self.created_at and self.released_at < self.created_at:
            raise ValidationError(
                {"released_at": "Release time cannot be before takeover time."}
            )
        if self.reopened_at and self.released_at and self.reopened_at < self.released_at:
            raise ValidationError(
                {"reopened_at": "Reopen time cannot be before release time."}
            )

    def __str__(self):
        if self.is_active:
            return f"{self.agent} — active on {self.chat_session_id}"
        return f"{self.agent} — {self.get_release_reason_display()} on {self.chat_session_id}"
