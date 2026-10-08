from django.db import models


class NotificationRecipientType(models.TextChoices):
    """The kind of audience a notification is addressed to."""
    GLOBAL = "global", "Global"
    CHATBOT = "chatbot", "Chatbot"
    USER = "user", "User"
    CHAT_SESSION = "chat_session", "Chat session"
    WORKSPACE = "workspace", "Workspace"


class NotificationType(models.TextChoices):
    """The event represented by a notification."""
    GENERAL = "general", "General"
    UPDATE = "update", "Update"
    MAINTENANCE = "maintenance", "Maintenance"
    NOTIFY = "notify", "Notify"
    NEW_MESSAGE = "new_message", "New message"
    SESSION_STARTED = "session_started", "Session started"
    AI_NOTIFICATION = "ai_notification", "AI notification"
    TRAINING_COMPLETE = "training_complete", "Training complete"
