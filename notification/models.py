from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from app.base.models import BaseModel


class NotificationRecipientType(models.TextChoices):
    """The kind of audience a notification is addressed to."""

    GLOBAL = "global", "Global"
    WORKSPACE = "workspace", "Workspace"
    CHATBOT = "chatbot", "Chatbot"
    USER = "user", "User"
    CHAT_SESSION = "chat_session", "Chat session"


class NotificationType(models.TextChoices):
    """The event represented by a notification."""

    GENERAL = "general", "General"
    UPDATE = "update", "Update"
    MAINTENANCE = "maintenance", "Maintenance"
    NOTIFY = "notify", "Notify"
    NEW_MESSAGE = "new_message", "New message"
    SESSION_ENDED = "session_ended", "Session ended"
    SESSION_STARTED = "session_started", "Session started"
    AI_NOTIFICATION = "ai_notification", "AI notification"


class Notification(BaseModel):
    recipient_type = models.CharField(
        max_length=20,
        choices=NotificationRecipientType.choices,
        db_index=True,
        help_text="The audience addressed by this notification.",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
        help_text="Set only when recipient_type is user.",
    )
    target_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Chat-session UUID until the chat-session model is available.",
    )
    workspace = models.ForeignKey(
        "workspace.Workspace",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(
        max_length=24,
        choices=NotificationType.choices,
        default=NotificationType.GENERAL,
        db_index=True,
        help_text="The event represented by the notification.",
    )
    title = models.CharField(max_length=180)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["recipient_type", "recipient", "-created_at"],
                name="notif_user_recipient_idx",
            ),
            models.Index(
                fields=["recipient_type", "workspace", "-created_at"],
                name="notif_workspace_scope_idx",
            ),
            models.Index(
                fields=["recipient_type", "chatbot", "-created_at"],
                name="notif_chatbot_scope_idx",
            ),
            models.Index(
                fields=["recipient_type", "target_id", "-created_at"],
                name="notif_session_scope_idx",
            ),
            models.Index(
                fields=["notification_type", "-created_at"],
                name="notif_event_type_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        recipient_type=NotificationRecipientType.GLOBAL,
                        recipient__isnull=True,
                        workspace__isnull=True,
                        chatbot__isnull=True,
                        target_id__isnull=True,
                    )
                    | models.Q(
                        recipient_type=NotificationRecipientType.USER,
                        recipient__isnull=False,
                        workspace__isnull=True,
                        chatbot__isnull=True,
                        target_id__isnull=True,
                    )
                    | models.Q(
                        recipient_type=NotificationRecipientType.WORKSPACE,
                        recipient__isnull=True,
                        workspace__isnull=False,
                        chatbot__isnull=True,
                        target_id__isnull=True,
                    )
                    | models.Q(
                        recipient_type=NotificationRecipientType.CHATBOT,
                        recipient__isnull=True,
                        workspace__isnull=True,
                        chatbot__isnull=False,
                        target_id__isnull=True,
                    )
                    | models.Q(
                        recipient_type=NotificationRecipientType.CHAT_SESSION,
                        recipient__isnull=True,
                        workspace__isnull=True,
                        chatbot__isnull=True,
                        target_id__isnull=False,
                    )
                ),
                name="notification_recipient_shape_is_valid",
            ),
        ]

    def clean(self):
        super().clean()

        if self.recipient_type == NotificationRecipientType.GLOBAL:
            if (
                self.recipient_id
                or self.workspace_id
                or self.chatbot_id
                or self.target_id
            ):
                raise ValidationError(
                    "Global notifications cannot have a scoped recipient."
                )
            return

        if self.recipient_type == NotificationRecipientType.USER:
            if not self.recipient_id:
                raise ValidationError("User notifications require a recipient.")
            if self.workspace_id or self.chatbot_id or self.target_id:
                raise ValidationError(
                    "User notifications cannot have another scoped recipient."
                )
            return

        if self.recipient_type == NotificationRecipientType.WORKSPACE:
            if self.recipient_id or self.chatbot_id or self.target_id:
                raise ValidationError(
                    "Workspace notifications can only have a workspace recipient."
                )
            if not self.workspace_id:
                raise ValidationError("Workspace notifications require a workspace.")
            return

        if self.recipient_type == NotificationRecipientType.CHATBOT:
            if self.recipient_id or self.workspace_id or self.target_id:
                raise ValidationError(
                    "Chatbot notifications can only have a chatbot recipient."
                )
            if not self.chatbot_id:
                raise ValidationError("Chatbot notifications require a chatbot.")
            return

        if self.recipient_type == NotificationRecipientType.CHAT_SESSION:
            if self.recipient_id or self.workspace_id or self.chatbot_id:
                raise ValidationError(
                    "Chat-session notifications can only have a target_id."
                )
            if not self.target_id:
                raise ValidationError(
                    "Chat-session notifications require target_id."
                )

    def __str__(self):
        if self.recipient_type == NotificationRecipientType.GLOBAL:
            target = "all users"
        elif self.recipient_type == NotificationRecipientType.USER:
            target = self.recipient
        elif self.recipient_type == NotificationRecipientType.WORKSPACE:
            target = self.workspace
        elif self.recipient_type == NotificationRecipientType.CHATBOT:
            target = self.chatbot
        else:
            target = f"{self.get_recipient_type_display()} {self.target_id}"
        return f"{self.title} -> {target}"


class NotificationRead(BaseModel):
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="read_receipts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_reads",
        db_index=True,
    )
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-read_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "user"],
                name="unique_notification_read_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "read_at"]),
        ]

    def __str__(self):
        return f"{self.user} read {self.notification_id}"
