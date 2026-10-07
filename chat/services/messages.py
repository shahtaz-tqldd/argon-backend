from django.core.exceptions import ValidationError
from django.db import transaction

from chat.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatSession,
    ChatSessionTakeover,
)
from chat.utils.choices import ChatMessageSenderType, ChatSessionStatus


ATTACHMENT_ROW_FIELDS = frozenset(
    (
        "attachment_type",
        "file_url",
        "file_name",
        "mime_type",
        "file_size",
        "duration_ms",
        "sort_order",
    )
)


def serialize_message_event(message):
    sender = None
    if message.sender_id:
        sender = {
            "id": str(message.sender_id),
            "user_id": str(message.sender.user_id),
            "name": message.sender.user.name,
            "email": message.sender.user.email,
            "avatar": getattr(message.sender.user.profile, "avatar_url", ""),
        }
    return {
        "id": str(message.id),
        "chat_session_id": str(message.chat_session_id),
        "sender_type": message.sender_type,
        "sender": sender,
        "content": message.content,
        "status": message.status,
        "external_id": message.external_id,
        "metadata": message.metadata,
        "attachments": [
            {
                "id": str(attachment.id),
                "attachment_type": attachment.attachment_type,
                "file_url": attachment.file_url,
                "file_name": attachment.file_name,
                "mime_type": attachment.mime_type,
                "file_size": attachment.file_size,
                "duration_ms": attachment.duration_ms,
                "sort_order": attachment.sort_order,
            }
            for attachment in message.attachments.all()
        ],
        "created_at": message.created_at.isoformat(),
        "updated_at": message.updated_at.isoformat(),
    }


def create_system_message(
    chat_session,
    *,
    content,
    event_type,
    metadata=None,
    visibility="internal",
):
    """Append an auditable lifecycle event to a conversation timeline."""
    event_metadata = {
        "event_type": event_type,
        **(metadata or {}),
        "visibility": visibility,
    }
    message = ChatMessage(
        chat_session=chat_session,
        sender_type=ChatMessageSenderType.SYSTEM,
        content=content,
        metadata=event_metadata,
    )
    message.full_clean()
    message.save()
    return message


def create_visitor_takeover_message(
    chat_session,
    *,
    system_message_type,
    actor_name,
    event_type,
):
    """Create a visitor-safe ownership message without private audit data."""
    return create_system_message(
        chat_session,
        content=f"{actor_name} took over the conversation.",
        event_type=event_type,
        visibility="public",
        metadata={
            "system_message_type": system_message_type,
            "actor_name": actor_name,
        },
    )


def create_chat_message(
    chat_session,
    *,
    sender_type,
    content,
    metadata=None,
    external_id="",
    sender=None,
    attachments=None,
):
    """Create a message and its attachment rows inside the caller's transaction.

    ``attachments`` are validated attachment payloads that reference files
    uploaded beforehand through the file API.
    """

    message = ChatMessage(
        chat_session=chat_session,
        sender_type=sender_type,
        sender=sender,
        content=content,
        metadata=metadata or {},
        external_id=external_id,
    )
    if attachments:
        message._pending_attachments = attachments
    message.full_clean()
    message.save()
    if attachments:
        ChatMessageAttachment.objects.bulk_create(
            ChatMessageAttachment(
                chat_message=message,
                **{
                    field: value
                    for field, value in attachment.items()
                    if field in ATTACHMENT_ROW_FIELDS
                },
            )
            for attachment in attachments
        )
    return message


def send_agent_message(
    chat_session,
    agent,
    *,
    content,
    metadata=None,
    attachments=None,
):
    with transaction.atomic():
        chat_session = (
            ChatSession.objects.select_for_update()
            .select_related("visitor")
            .get(pk=chat_session.pk)
        )
        if chat_session.status in {
            ChatSessionStatus.RESOLVED,
            ChatSessionStatus.CLOSED,
        }:
            raise ValidationError("Cannot reply to a resolved or closed session.")

        active_takeover = ChatSessionTakeover.objects.filter(
            chat_session=chat_session,
            agent=agent,
            agent__is_active=True,
            agent__user__is_active=True,
            released_at__isnull=True,
        ).first()
        if active_takeover is None:
            raise ValidationError(
                "You must be the active takeover agent before replying."
            )

        if (
            chat_session.visitor is not None
            and chat_session.visitor.is_blocked
        ):
            raise ValidationError(
                "Cannot send a message to a blocked visitor."
            )

        message = create_chat_message(
            chat_session,
            sender_type=ChatMessageSenderType.AGENT,
            sender=agent,
            content=content,
            metadata=metadata,
            attachments=attachments,
        )
    return message
