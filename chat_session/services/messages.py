from django.core.exceptions import ValidationError
from django.db import transaction

from chat_session.models import ChatMessage, ChatSession, ChatSessionTakeover
from chat_session.utils.choices import ChatMessageSenderType, ChatSessionStatus


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
        "attachments": [],
        "created_at": message.created_at.isoformat(),
        "updated_at": message.updated_at.isoformat(),
    }


def send_agent_message(chat_session, agent, *, content, metadata=None):
    with transaction.atomic():
        chat_session = ChatSession.objects.select_for_update().get(
            pk=chat_session.pk
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

        message = ChatMessage(
            chat_session=chat_session,
            sender_type=ChatMessageSenderType.AGENT,
            sender=agent,
            content=content,
            metadata=metadata or {},
        )
        message.full_clean()
        message.save()
    return message
