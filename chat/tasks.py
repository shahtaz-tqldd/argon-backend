from app.utils.logger import logger

from celery import shared_task
from django.db import transaction

from agent.client import AgentClient
from agent.helpers.global_tools import _request_human_escalation
from chatbot.models import ChatbotConfig
from chat.models import ChatMessage, ChatSession
from chat.services.events import publish_session_event
from chat.services.messages import create_chat_message
from chat.utils.choices import ChatMessageSenderType, ChatSessionStatus


def is_ai_reply_enabled(session, chatbot=None):
    if session.is_test:
        return False
    if session.status != ChatSessionStatus.OPEN:
        return False
    if session.assigned_to_id:
        return False
    chatbot = chatbot or session.chatbot
    return bool(session.ai_enabled and chatbot.ai_enabled and chatbot.is_active)


def _attachment_info(visitor_message):
    """Whether the message has attachments, plus their distinct types."""
    types = list(
        dict.fromkeys(
            visitor_message.attachments.values_list(
                "attachment_type",
                flat=True,
            ).order_by("sort_order", "created_at")
        )
    )
    return bool(types), types


def _generate_reply(session, visitor_message):
    has_attachments, attachment_types = _attachment_info(visitor_message)
    return AgentClient(
        session.chatbot,
        session,
        has_attachments=has_attachments,
        attachment_types=attachment_types,
    ).generate_reply_sync(
        visitor_message=visitor_message,
        user_id=session.visitor_id or str(session.id),
    )


def _attachment_notice_content(chatbot, attachment_types):
    received = (
        f" (received: {', '.join(attachment_types)})"
        if attachment_types
        else ""
    )
    if getattr(chatbot, "human_handoff_enabled", True):
        return (
            f"I can't access the attachments you shared{received}. However, "
            "I have notified our customer support team to review them for "
            "you."
        )
    return (
        f"I can't access the attachments you shared{received}. Could you "
        "describe what they contain?"
    )


@transaction.atomic
def _generate_attachment_notice_reply(
    session,
    chatbot,
    visitor_message,
    attachment_types,
):
    """Attachment-only messages need no agent run: reply with a canned notice
    and request human escalation directly.

    Mirrors AgentClient._persist_reply's checks and idempotency — the reply
    uses the same ``ai:{message id}`` external_id, and the session row lock
    serializes concurrent attempts.
    """
    session = ChatSession.objects.select_for_update().get(
        pk=session.pk,
        chatbot_id=chatbot.id,
    )
    external_id = f"ai:{visitor_message.id}"
    existing = ChatMessage.objects.filter(
        chat_session=session,
        external_id=external_id,
    ).first()
    if existing is not None:
        return {"content": existing.content, "metadata": existing.metadata}

    if session.is_test or session.status != ChatSessionStatus.OPEN:
        return None
    if (
        session.assigned_to_id
        or not session.ai_enabled
        or not chatbot.ai_enabled
        or not chatbot.is_active
    ):
        return None

    metadata = {"attachments_notice": {"types": attachment_types}}
    if getattr(chatbot, "human_handoff_enabled", True):
        escalation_reason = (
            "Visitor sent attachment(s) the assistant cannot access: "
            f"{', '.join(attachment_types) or 'unknown type'}."
        )
        # Escalate first so the "notified" claim in the reply is true.
        _request_human_escalation(
            chatbot.id,
            session.id,
            escalation_reason,
        )
        metadata["escalation"] = {"reason": escalation_reason}

    message = create_chat_message(
        session,
        sender_type=ChatMessageSenderType.AI,
        content=_attachment_notice_content(chatbot, attachment_types),
        metadata=metadata,
        external_id=external_id,
    )
    return {"content": message.content, "metadata": message.metadata}


def dispatch_ai_reply(visitor_message_id):
    """Queue an AgentClient response for a visitor message."""
    generate_ai_reply_task.delay(str(visitor_message_id))
    return True


def dispatch_appointment_reply(chat_session_id, appointment_id):
    """Queue the conversational acknowledgment for a saved appointment."""
    generate_ai_reply_task.delay(
        chat_session_id=str(chat_session_id),
        appointment_id=str(appointment_id),
    )
    return True


def _reserve_ai_message(chatbot_id):
    with transaction.atomic():
        capacity, _ = ChatbotConfig.objects.select_for_update().get_or_create(
            chatbot_id=chatbot_id
        )
        if (
            capacity.ai_message_limit is not None
            and capacity.current_ai_message_count >= capacity.ai_message_limit
        ):
            return False
        capacity.current_ai_message_count += 1
        capacity.save(update_fields=["current_ai_message_count", "updated_at"])
        return True


def _release_ai_message(chatbot_id):
    with transaction.atomic():
        capacity = ChatbotConfig.objects.select_for_update().get(
            chatbot_id=chatbot_id
        )
        if capacity.current_ai_message_count:
            capacity.current_ai_message_count -= 1
            capacity.save(update_fields=["current_ai_message_count", "updated_at"])


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def generate_ai_reply_task(
    self,
    visitor_message_id=None,
    *,
    chat_session_id=None,
    appointment_id=None,
):
    if appointment_id is not None:
        if visitor_message_id is not None or chat_session_id is None:
            raise ValueError(
                "Appointment replies require chat_session_id and appointment_id only."
            )
        session = (
            ChatSession.objects.select_related("chatbot")
            .filter(pk=chat_session_id, is_test=False)
            .first()
        )
        if session is None:
            return None

        external_id = f"appointment:{appointment_id}"
        existing_reply = ChatMessage.objects.filter(
            chat_session=session,
            external_id=external_id,
        ).first()
        if existing_reply is not None:
            return {
                "content": existing_reply.content,
                "metadata": existing_reply.metadata,
            }

        publish_session_event(
            session.id,
            session.chatbot_id,
            "ai.response.started",
            {"appointment_id": str(appointment_id)},
        )
        try:
            return AgentClient(
                session.chatbot,
                session,
            ).generate_booking_reply_sync(
                appointment_id=str(appointment_id),
                user_id=session.visitor_id or str(session.id),
            )
        except Exception:
            logger.exception(
                "Appointment reply failed for appointment %s",
                appointment_id,
            )
            if self.request.retries >= 2:
                publish_session_event(
                    session.id,
                    session.chatbot_id,
                    "ai.response.failed",
                    {"code": "generation_failed", "retryable": True},
                )
            raise

    if visitor_message_id is None or chat_session_id is not None:
        raise ValueError("Visitor replies require visitor_message_id only.")

    visitor_message = (
        ChatMessage.objects.select_related("chat_session__chatbot")
        .filter(
            pk=visitor_message_id,
            sender_type=ChatMessageSenderType.VISITOR,
        )
        .first()
    )
    if visitor_message is None:
        return None

    session = visitor_message.chat_session
    chatbot = session.chatbot
    if session.is_test:
        return None

    existing_reply = ChatMessage.objects.filter(
        chat_session=session,
        external_id=f"ai:{visitor_message.id}",
    ).first()
    if existing_reply is not None:
        return {
            "content": existing_reply.content,
            "metadata": existing_reply.metadata,
        }

    if not is_ai_reply_enabled(session, chatbot):
        return None

    # An attachment-only message (no text) has nothing for the agent to work
    # with: reply with the canned notice and escalate, without an LLM call
    # or an AI message reservation.
    if not visitor_message.content.strip():
        has_attachments, attachment_types = _attachment_info(visitor_message)
        if not has_attachments:
            return None
        publish_session_event(
            session.id,
            chatbot.id,
            "ai.response.started",
            {"in_reply_to": str(visitor_message.id)},
        )
        return _generate_attachment_notice_reply(
            session,
            chatbot,
            visitor_message,
            attachment_types,
        )

    if not _reserve_ai_message(chatbot.id):
        publish_session_event(
            session.id,
            chatbot.id,
            "ai.response.failed",
            {"code": "message_limit_reached", "retryable": False},
        )
        return None

    publish_session_event(
        session.id,
        chatbot.id,
        "ai.response.started",
        {"in_reply_to": str(visitor_message.id)},
    )
    try:
        reply = _generate_reply(
            session,
            visitor_message,
        )
        if reply is None:
            _release_ai_message(chatbot.id)
            return None
        return reply

    except Exception:
        _release_ai_message(chatbot.id)
        logger.exception("AI reply failed for visitor message %s", visitor_message.id)
        if self.request.retries >= 2:
            publish_session_event(
                session.id,
                chatbot.id,
                "ai.response.failed",
                {"code": "generation_failed", "retryable": True},
            )
        raise
