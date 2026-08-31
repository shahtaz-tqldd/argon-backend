from django.db import models


class ChatSessionChannel(models.TextChoices):
    WEB_WIDGET = "web_widget", "Web widget"
    MESSENGER = "messenger", "Messenger"
    INSTAGRAM = "instagram", "Instagram"
    WHATSAPP = "whatsapp", "WhatsApp"
    API = "api", "API"


class ChatSessionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    NEED_ATTENTION = "need_attention", "Need Attention"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class ChatMessageSenderType(models.TextChoices):
    VISITOR = "visitor", "Visitor"
    AI = "ai", "AI"
    AGENT = "agent", "Agent"
    SYSTEM = "system", "System"


class ChatMessageStatus(models.TextChoices):
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    READ = "read", "Read"
    FAILED = "failed", "Failed"


class ChatSessionResolutionType(models.TextChoices):
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class ChatMessageAttachmentType(models.TextChoices):
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    AUDIO = "audio", "Audio"
    DOCUMENT = "document", "Document"
    OTHER = "other", "Other"


class ChatSessionTakeoverReleaseReason(models.TextChoices):
    REASSIGNED = "reassigned", "Reassigned"
    MANUAL_RELEASE = "manual_release", "Manual Release"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"
