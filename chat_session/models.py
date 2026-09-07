from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from app.base.models import BaseMinModel
from chat_session.utils.choices import (
    ChatMessageAttachmentType,
    ChatMessageSenderType,
    ChatMessageStatus,
    ChatSessionChannel,
    ChatSessionStatus,
    ChatSessionAttentionReason,
    ChatSessionTakeoverReleaseReason,
    ChatSessionTransferStatus,
)
from chat_session.utils.validators import validate_json_object


class ChatSession(BaseMinModel):
    """A single conversation between a visitor and a chatbot"""

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        on_delete=models.CASCADE,
        related_name="chat_sessions",
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

    channel = models.CharField(
        max_length=20,
        choices=ChatSessionChannel.choices,
        default=ChatSessionChannel.WEB_WIDGET,
    )

    status = models.CharField(
        max_length=20,
        choices=ChatSessionStatus.choices,
        default=ChatSessionStatus.OPEN,
        db_index=True,
    )

    # Human attention
    requires_attention = models.BooleanField(default=False)
    attention_reason = models.CharField(
        max_length=30,
        choices=ChatSessionAttentionReason.choices,
        blank=True,
        default="",
    )
    attention_requested_at = models.DateTimeField(null=True, blank=True)

    # Ownership — the human agent currently responsible for this session.
    # Kept in sync with ChatSessionTakeover (one active takeover row per
    # session) by the service layer; denormalized here so inbox/agent-queue
    # queries don't need a join against the takeover table.
    assigned_to = models.ForeignKey(
        "chatbot.ChatbotUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_chat_sessions",
        help_text="Human agent currently responsible for this session, if any.",
    )

    # Activity
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_visitor_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    # Lifecycle timestamps
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    ai_enabled = models.BooleanField(default=True)

    # Session User/Lead
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

    user_metadata = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
        help_text=(
            "Visitor-side context captured before/without a Lead — IP, "
            "detected location, name, email, browser, etc."
        ),
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
        help_text="Internal/channel-specific session context stored as a JSON object.",
    )

    @property
    def is_recently_active(self):
        if not self.last_visitor_activity_at:
            return False

        return (
            self.last_visitor_activity_at
            >= timezone.now() - timedelta(minutes=10)
        )

    class Meta:
        ordering = ["-last_activity_at", "-created_at"]
        indexes = [
            # Primary inbox listing: all sessions for a chatbot, by status,
            # newest activity first.
            models.Index(
                fields=["chatbot", "status", "-last_activity_at"],
                name="chat_session_inbox_idx",
            ),
            # "Needs attention" queue — distinct from the general inbox
            # because it's filtered/polled independently (e.g. dashboard
            # badge, alerting) and status alone doesn't cover it.
            models.Index(
                fields=["chatbot", "requires_attention", "-last_activity_at"],
                name="chat_session_attention_idx",
            ),
            # An agent's personal queue.
            models.Index(
                fields=["assigned_to", "status", "-last_activity_at"],
                name="chat_session_agent_idx",
            ),
            models.Index(
                fields=["chatbot", "visitor_id"],
                name="chat_session_visitor_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["chatbot", "channel", "external_thread_id"],
                condition=~Q(external_thread_id=""),
                name="unique_external_thread_per_channel",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        requires_attention=False,
                        attention_reason="",
                        attention_requested_at__isnull=True,
                    )
                    | Q(
                        requires_attention=True,
                        attention_reason__gt="",
                        attention_requested_at__isnull=False,
                    )
                ),
                name="chat_session_attention_fields_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=ChatSessionStatus.OPEN,
                        resolved_at__isnull=True,
                        closed_at__isnull=True,
                    )
                    | Q(
                        status=ChatSessionStatus.RESOLVED,
                        resolved_at__isnull=False,
                        closed_at__isnull=True,
                    )
                    | Q(
                        status=ChatSessionStatus.CLOSED,
                        resolved_at__isnull=True,
                        closed_at__isnull=False,
                    )
                ),
                name="chat_session_lifecycle_fields_consistent",
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
        has_content = bool(self.content and self.content.strip())

        # Attachments are separate rows FK'd to this message, so they can
        # only exist once this row has a pk. On update, we can genuinely
        # check for at least one attachment. On creation, there's nothing
        # to check yet — the caller (serializer/service) is responsible for
        # ensuring content or attachments are supplied within the same
        # transaction before commit.
        if self.pk:
            has_attachments = self.attachments.exists()
            if not has_content and not has_attachments:
                raise ValidationError(
                    {"content": "Message must have content or at least one attachment."}
                )
        elif not has_content:
            raise ValidationError(
                {"content": (
                    "Message must have content, or have attachments created "
                    "in the same transaction as this message."
                )}
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
        if not is_new:
            return

        # Bulk .update() instead of loading + saving the session: avoids a
        # second SELECT, a full model save, and any save-signal cascade on
        # what is the hottest write path in the schema.
        updates = {"last_activity_at": self.created_at}
        if self.sender_type == ChatMessageSenderType.VISITOR:
            updates["last_visitor_activity_at"] = self.created_at

        ChatSession.objects.filter(
            pk=self.chat_session_id,
            last_activity_at__lt=self.created_at,
        ).update(**updates)

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
    duration_ms = models.PositiveIntegerField(
        null=True, blank=True, help_text="For audio/video attachments."
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "created_at"]
    def __str__(self):
        return self.file_name or self.file_url


class ChatSessionTakeover(BaseMinModel):
    """
    Tracks human ownership of a session over time. Exactly one row per
    session may be "active" (released_at is null) at a time — enforced by
    unique_active_takeover_per_session below, which is what forces a
    take-over-before-resolve workflow: resolving is just releasing this row.
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

    # release
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.CharField(
        max_length=20,
        choices=ChatSessionTakeoverReleaseReason.choices,
        blank=True,
        default="",
    )
    released_to = models.ForeignKey(
        "chatbot.ChatbotUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="incoming_chat_takeovers",
        help_text="The agent this session was handed to, when release_reason=TRANSFERRED.",
    )
    reopened_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set if a session resolved/closed under this takeover was reopened.",
    )
    resolution_note = models.TextField(blank=True, default="")

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
            # Only one open takeover per session at a time.
            models.UniqueConstraint(
                fields=["chat_session"],
                condition=Q(released_at__isnull=True),
                name="unique_active_takeover_per_session",
            ),
            # released_at and release_reason must be set together.
            models.CheckConstraint(
                condition=(
                    Q(released_at__isnull=True, release_reason="")
                    | Q(released_at__isnull=False, release_reason__gt="")
                ),
                name="chat_takeover_release_fields_consistent",
            ),
            # reopened_* only valid on rows released as RESOLVED/CLOSED.
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
            # released_to is only meaningful for a TRANSFERRED release.
            models.CheckConstraint(
                condition=(
                    Q(released_to__isnull=True)
                    | Q(release_reason=ChatSessionTakeoverReleaseReason.TRANSFERRED)
                ),
                name="chat_takeover_released_to_requires_transfer",
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
        if self.released_to_id:
            if self.release_reason != ChatSessionTakeoverReleaseReason.TRANSFERRED:
                raise ValidationError(
                    {"released_to": "Only set when release_reason is TRANSFERRED."}
                )
            if (
                self.chat_session_id
                and self.released_to.chatbot_id != self.chat_session.chatbot_id
            ):
                raise ValidationError(
                    {"released_to": "The agent must belong to the session's chatbot."}
                )
        if (
            self.released_at
            and self.created_at
            and self.released_at < self.created_at
        ):
            raise ValidationError(
                {"released_at": "Release time cannot be before takeover time."}
            )
        if (
            self.reopened_at
            and self.released_at
            and self.reopened_at < self.released_at
        ):
            raise ValidationError(
                {"reopened_at": "Reopen time cannot be before release time."}
            )

    def __str__(self):
        if self.is_active:
            return f"{self.agent} — active on {self.chat_session_id}"
        return (
            f"{self.agent} — {self.get_release_reason_display()} "
            f"on {self.chat_session_id}"
        )


class ChatSessionTransfer(BaseMinModel):
    """A request to hand a session from one agent to another."""
    chat_session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="transfers",
    )

    from_agent = models.ForeignKey(
        "chatbot.ChatbotUser",
        on_delete=models.CASCADE,
        related_name="outgoing_chat_transfers",
    )

    to_agent = models.ForeignKey(
        "chatbot.ChatbotUser",
        on_delete=models.CASCADE,
        related_name="incoming_chat_transfers",
    )

    status = models.CharField(
        max_length=20,
        choices=ChatSessionTransferStatus.choices,
        default=ChatSessionTransferStatus.PENDING,
        db_index=True,
    )

    reason = models.TextField(blank=True, default="")

    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chat_session", "-created_at"],
                name="chat_transfer_session_idx",
            ),
            # An agent's incoming-transfer inbox.
            models.Index(
                fields=["to_agent", "status", "-created_at"],
                name="chat_transfer_to_agent_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["chat_session"],
                condition=Q(status=ChatSessionTransferStatus.PENDING),
                name="unique_pending_transfer_per_session",
            ),
            models.CheckConstraint(
                condition=~Q(from_agent=models.F("to_agent")),
                name="chat_transfer_distinct_agents",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=ChatSessionTransferStatus.PENDING,
                        completed_at__isnull=True,
                    )
                    | (
                        ~Q(status=ChatSessionTransferStatus.PENDING)
                        & Q(completed_at__isnull=False)
                    )
                ),
                name="chat_transfer_completed_at_consistent",
            ),
        ]

    @property
    def is_expired(self):
        return bool(
            self.status == ChatSessionTransferStatus.PENDING
            and self.expires_at
            and self.expires_at < timezone.now()
        )

    def clean(self):
        super().clean()
        if (
            self.from_agent_id
            and self.to_agent_id
            and self.from_agent_id == self.to_agent_id
        ):
            raise ValidationError(
                {"to_agent": "Cannot transfer a session to the same agent."}
            )
        if self.chat_session_id:
            if (
                self.from_agent_id
                and self.from_agent.chatbot_id != self.chat_session.chatbot_id
            ):
                raise ValidationError(
                    {"from_agent": "The agent must belong to the session's chatbot."}
                )
            if (
                self.to_agent_id
                and self.to_agent.chatbot_id != self.chat_session.chatbot_id
            ):
                raise ValidationError(
                    {"to_agent": "The agent must belong to the session's chatbot."}
                )
        if (
            self.status != ChatSessionTransferStatus.PENDING
            and not self.completed_at
        ):
            raise ValidationError(
                {"completed_at": "Required once a transfer is no longer pending."}
            )

    def __str__(self):
        return f"{self.from_agent} → {self.to_agent} ({self.get_status_display()})"
