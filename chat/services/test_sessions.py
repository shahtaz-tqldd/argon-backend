from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from agent.client import AgentClient
from chatbot.models import ChatbotCapacity
from chat.models import ChatMessage, ChatSession
from chat.utils.choices import ChatMessageSenderType, ChatSessionStatus


TEST_ALLOWANCE = "test_allowance"
SUBSCRIPTION_ALLOWANCE = "subscription_allowance"


class TestChatMessageLimitExceeded(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TestChatReply:
    visitor_message: ChatMessage
    ai_message: ChatMessage
    usage_source: str
    capacity: ChatbotCapacity


@transaction.atomic
def create_test_session(chatbot):
    session = ChatSession(
        chatbot=chatbot,
        is_test=True,
        ai_enabled=chatbot.ai_enabled,
    )
    session.full_clean()
    session.save()
    return session


@transaction.atomic
def _reserve_test_ai_message(chatbot_id):
    capacity, created = ChatbotCapacity.objects.get_or_create(
        chatbot_id=chatbot_id,
    )
    if not created:
        capacity = ChatbotCapacity.objects.select_for_update().get(
            pk=capacity.pk,
        )

    if capacity.current_test_ai_message_count < capacity.test_ai_message_limit:
        capacity.current_test_ai_message_count += 1
        capacity.save(
            update_fields=["current_test_ai_message_count", "updated_at"],
        )
        return TEST_ALLOWANCE

    if (
        capacity.ai_message_limit is not None
        and capacity.current_ai_message_count >= capacity.ai_message_limit
    ):
        return None

    capacity.current_ai_message_count += 1
    capacity.save(update_fields=["current_ai_message_count", "updated_at"])
    return SUBSCRIPTION_ALLOWANCE


@transaction.atomic
def _release_test_ai_message(chatbot_id, usage_source):
    capacity = ChatbotCapacity.objects.select_for_update().get(
        chatbot_id=chatbot_id,
    )
    if usage_source == TEST_ALLOWANCE:
        if capacity.current_test_ai_message_count:
            capacity.current_test_ai_message_count -= 1
            capacity.save(
                update_fields=["current_test_ai_message_count", "updated_at"],
            )
        return
    if (
        usage_source == SUBSCRIPTION_ALLOWANCE
        and capacity.current_ai_message_count
    ):
        capacity.current_ai_message_count -= 1
        capacity.save(update_fields=["current_ai_message_count", "updated_at"])


def _generate_test_reply(session, visitor_message):
    response = AgentClient(session.chatbot, session).chat_sync(
        message=visitor_message.content,
        user_id=f"test:{session.id}",
    )
    result = response["result"]
    return result["content"], {
        "in_reply_to": str(visitor_message.id),
        "source_ids": result.get("source_ids", []),
        "appointment": result.get("appointment"),
        "test_message": True,
    }


def send_test_message(chat_session, *, content):
    chat_session = ChatSession.objects.select_related("chatbot").get(
        pk=chat_session.pk,
        is_test=True,
    )
    if chat_session.status != ChatSessionStatus.OPEN:
        raise ValidationError("Cannot send a message to an ended test session.")
    if not chat_session.ai_enabled or not chat_session.chatbot.ai_enabled:
        raise ValidationError("AI replies are disabled for this chatbot.")
    if not chat_session.chatbot.is_active:
        raise ValidationError("Chatbot is deactivated.")

    usage_source = _reserve_test_ai_message(chat_session.chatbot_id)
    if usage_source is None:
        raise TestChatMessageLimitExceeded(
            "The free test allowance and subscription AI message allowance "
            "have been exhausted."
        )

    visitor_message = ChatMessage(
        chat_session=chat_session,
        sender_type=ChatMessageSenderType.VISITOR,
        content=content,
        metadata={"test_message": True},
    )
    try:
        visitor_message.full_clean()
        visitor_message.save()
        reply_content, reply_metadata = _generate_test_reply(
            chat_session,
            visitor_message,
        )
        with transaction.atomic():
            locked_session = ChatSession.objects.select_for_update().get(
                pk=chat_session.pk,
                is_test=True,
                status=ChatSessionStatus.OPEN,
            )
            ai_message = ChatMessage(
                chat_session=locked_session,
                sender_type=ChatMessageSenderType.AI,
                content=reply_content,
                metadata=reply_metadata,
                external_id=f"test-ai:{visitor_message.id}",
            )
            ai_message.full_clean()
            ai_message.save()
    except Exception:
        _release_test_ai_message(chat_session.chatbot_id, usage_source)
        raise

    capacity = ChatbotCapacity.objects.get(chatbot_id=chat_session.chatbot_id)
    return TestChatReply(
        visitor_message=visitor_message,
        ai_message=ai_message,
        usage_source=usage_source,
        capacity=capacity,
    )
