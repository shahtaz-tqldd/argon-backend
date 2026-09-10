from app.utils.logger import logger

from celery import shared_task
from django.conf import settings
from django.db import transaction

from agent.client import AgentClient
from analytics.choices import AIUsageType
from analytics.services.ai_usage import record_ai_usage
from chatbot.models import ChatbotCapacity
from chat.models import ChatMessage, ChatSession
from chat.services.events import publish_session_event
from chat.utils.choices import ChatMessageSenderType, ChatSessionStatus


def is_ai_reply_enabled(session, chatbot=None):
    if session.status != ChatSessionStatus.OPEN:
        return False
    if session.assigned_to_id:
        return False
    chatbot = chatbot or session.chatbot
    return bool(session.ai_enabled and chatbot.ai_enabled and chatbot.is_active)


def _generate_reply(session, visitor_message):
    response = AgentClient(session.chatbot, session).chat_sync(
        message=visitor_message.content,
        user_id=session.visitor_id or str(session.id),
    )
    result = response["result"]
    metadata = {
        "in_reply_to": str(visitor_message.id),
        "source_ids": result.get("source_ids", []),
        "appointment": result.get("appointment"),
    }
    return result["content"], metadata, response["token"], response["cost"]


def dispatch_ai_reply(visitor_message_id):
    """Queue an AgentClient response for a visitor message."""
    generate_ai_reply_task.delay(str(visitor_message_id))
    return True


def _reserve_ai_message(chatbot_id):
    with transaction.atomic():
        capacity, _ = ChatbotCapacity.objects.select_for_update().get_or_create(
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
        capacity = ChatbotCapacity.objects.select_for_update().get(
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
def generate_ai_reply_task(self, visitor_message_id):
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
    external_id = f"ai:{visitor_message.id}"
    if ChatMessage.objects.filter(
        chat_session=session,
        external_id=external_id,
    ).exists():
        return None
    if not is_ai_reply_enabled(session, chatbot):
        return None

    capacity_reserved = True
    if capacity_reserved and not _reserve_ai_message(chatbot.id):
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
        content, metadata, token_usage, cost = _generate_reply(
            session,
            visitor_message,
        )
        with transaction.atomic():
            locked_session = ChatSession.objects.select_for_update().get(
                pk=session.pk
            )
            if not is_ai_reply_enabled(locked_session):
                if capacity_reserved:
                    _release_ai_message(chatbot.id)
                return None
            message = ChatMessage(
                chat_session=locked_session,
                sender_type=ChatMessageSenderType.AI,
                content=content,
                metadata=metadata,
                external_id=external_id,
            )
            message.full_clean()
            message.save()
            record_ai_usage(
                chatbot=chatbot,
                chat_session=locked_session,
                chat_message=message,
                usage_type=AIUsageType.CHAT,
                cost=cost,
                token_usage=token_usage,
                model=settings.GEMINI_CHAT_MODEL,
            )
        return str(message.id)
    
    except Exception:
        if capacity_reserved:
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
