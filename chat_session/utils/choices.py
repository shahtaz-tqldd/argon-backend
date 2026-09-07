from django.db import models


class ChatSessionChannel(models.TextChoices):
    WEB_WIDGET = "web_widget", "Web Widget"
    MESSENGER = "messenger", "Messenger"
    INSTAGRAM = "instagram", "Instagram"
    WHATSAPP = "whatsapp", "WhatsApp"
    API = "api", "API"


class ChatSessionStatus(models.TextChoices):
    """
    Lifecycle of the conversation/thread.
    """
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class ChatSessionAttentionReason(models.TextChoices):
    """
    Why human attention was requested.

    Blank value on ChatSession means no attention is currently required.
    """
    HUMAN_REQUESTED = "human_requested", "Human Requested"
    AI_UNCERTAIN = "ai_uncertain", "AI Unable to Answer"
    TOOL_FAILED = "tool_failed", "Tool Failed"
    ESCALATED = "escalated", "Escalated"
    OTHER = "other", "Other"


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


class ChatMessageAttachmentType(models.TextChoices):
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    AUDIO = "audio", "Audio"
    DOCUMENT = "document", "Document"
    OTHER = "other", "Other"


class ChatSessionTakeoverReleaseReason(models.TextChoices):
    """
    Why a human agent stopped owning the conversation.
    """
    TRANSFERRED = "transferred", "Transferred"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"
    RELEASED = "released", "Released"


class ChatSessionTransferStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
