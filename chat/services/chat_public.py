from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import Http404

from chat.models import ChatbotBlockedVisitor, ChatMessage, ChatSession
from chat.services.events import publish_session_event
from chat.services.visitor_tokens import (
    InvalidConversationToken,
    decode_conversation_token,
)
from chat.utils.choices import (
    ChatMessageSenderType,
    ChatSessionChannel,
    ChatSessionStatus,
)


RESUMABLE_SESSION_STATUSES = (
    ChatSessionStatus.OPEN,
)


def _get_visitor_sessions(chatbot, visitor_id):
    direct_sessions = ChatSession.objects.filter(
        chatbot=chatbot,
        visitor_id=visitor_id,
        channel=ChatSessionChannel.WEB_WIDGET,
        is_test=False,
    )
    anchor = direct_sessions.select_related("lead").first()
    if anchor is None:
        raise Http404("Visitor not found.")

    lead_session = (
        direct_sessions.filter(lead__isnull=False)
        .select_related("lead")
        .first()
    )
    lead = lead_session.lead if lead_session is not None else None
    filters = Q(visitor_id=visitor_id)
    if lead is not None:
        filters |= Q(lead=lead)
    sessions = ChatSession.objects.filter(
        filters,
        chatbot=chatbot,
        channel=ChatSessionChannel.WEB_WIDGET,
        is_test=False,
    )
    return anchor, lead, sessions


def get_public_visitor_details(chatbot, visitor_id):
    anchor, lead, _sessions = _get_visitor_sessions(chatbot, visitor_id)
    return {
        "visitor_id": visitor_id,
        "lead_id": lead.id if lead is not None else None,
        "lead_data": lead.collected_fields if lead is not None else {},
        "user_metadata": anchor.user_metadata or {},
    }


def get_public_visitor_sessions(chatbot, visitor_id):
    _anchor, _lead, sessions = _get_visitor_sessions(chatbot, visitor_id)
    last_message = (
        ChatMessage.objects.filter(chat_session=OuterRef("pk"))
        .exclude(metadata__contains={"visibility": "internal"})
        .order_by("-created_at", "-id")
    )
    return sessions.select_related("lead", "chatbot").annotate(
        message_count=Count(
            "messages",
            filter=~Q(
                messages__metadata__contains={"visibility": "internal"}
            ),
        ),
        last_message_sender_type=Subquery(
            last_message.values("sender_type")[:1]
        ),
        last_message_content=Subquery(last_message.values("content")[:1]),
        last_message_created_at=Subquery(
            last_message.values("created_at")[:1]
        ),
        last_message_agent_name=Subquery(
            last_message.values("sender__user__name")[:1]
        ),
    )


@transaction.atomic
def create_public_visitor_session(
    chatbot,
    visitor_id,
    *,
    user_metadata=None,
    metadata=None,
):
    if ChatbotBlockedVisitor.objects.filter(
        chatbot=chatbot,
        visitor_id=visitor_id,
    ).exists():
        raise ValidationError("This visitor has been blocked.")

    existing_sessions = ChatSession.objects.filter(
        chatbot=chatbot,
        visitor_id=visitor_id,
        channel=ChatSessionChannel.WEB_WIDGET,
        is_test=False,
    )
    anchor = existing_sessions.select_related("lead").first()
    lead_session = (
        existing_sessions.filter(lead__isnull=False)
        .select_related("lead")
        .first()
    )
    lead = lead_session.lead if lead_session is not None else None
    inherited_user_metadata = (
        anchor.user_metadata if anchor is not None else {}
    )
    session = ChatSession(
        chatbot=chatbot,
        channel=ChatSessionChannel.WEB_WIDGET,
        visitor_id=visitor_id,
        lead=lead,
        ai_enabled=chatbot.ai_enabled,
        user_metadata=(
            user_metadata
            if user_metadata is not None
            else inherited_user_metadata
        ),
        metadata=metadata or {},
    )
    session.full_clean()
    session.save()
    transaction.on_commit(
        lambda: publish_session_event(
            session.id,
            chatbot.id,
            "session.created",
            {
                "chatbot_id": str(chatbot.id),
                "channel": session.channel,
                "status": session.status,
            },
        )
    )
    return session


def get_visitor_conversation(chatbot, conversation_token):
    payload = decode_conversation_token(conversation_token)
    if payload["chatbot_id"] != str(chatbot.id):
        raise InvalidConversationToken(
            "The conversation token does not belong to this chatbot."
        )
    try:
        return ChatSession.objects.get(
            pk=payload["session_id"],
            chatbot=chatbot,
            visitor_id=payload["visitor_id"],
            channel=ChatSessionChannel.WEB_WIDGET,
            is_test=False,
        )
    except ChatSession.DoesNotExist as exc:
        raise Http404("Conversation not found.") from exc


def get_visitor_chat_session(chatbot, session_id, conversation_token):
    payload = decode_conversation_token(conversation_token)
    if (
        payload["session_id"] != str(session_id)
        or payload["chatbot_id"] != str(chatbot.id)
    ):
        raise PermissionDenied(
            "The conversation token does not belong to this conversation."
        )
    try:
        return ChatSession.objects.get(
            pk=session_id,
            chatbot=chatbot,
            visitor_id=payload["visitor_id"],
            channel=ChatSessionChannel.WEB_WIDGET,
            is_test=False,
            status__in=RESUMABLE_SESSION_STATUSES,
        )
    except ChatSession.DoesNotExist as exc:
        raise Http404("Conversation not found.") from exc


@transaction.atomic
def send_visitor_message(
    chat_session,
    *,
    content,
    metadata=None,
    external_id="",
):
    chat_session = ChatSession.objects.select_for_update().get(
        pk=chat_session.pk,
        is_test=False,
    )
    if chat_session.status not in RESUMABLE_SESSION_STATUSES:
        raise ValidationError("Cannot send a message to an ended conversation.")

    if ChatbotBlockedVisitor.objects.filter(
        chatbot_id=chat_session.chatbot_id,
        visitor_id=chat_session.visitor_id,
    ).exists():
        raise ValidationError("This visitor has been blocked from sending messages.")

    if external_id:
        existing = ChatMessage.objects.filter(
            chat_session=chat_session,
            external_id=external_id,
            sender_type=ChatMessageSenderType.VISITOR,
        ).first()
        if existing is not None:
            return existing, False

    message = ChatMessage(
        chat_session=chat_session,
        sender_type=ChatMessageSenderType.VISITOR,
        content=content,
        metadata=metadata or {},
        external_id=external_id,
    )
    message.full_clean()
    message.save()
    return message, True
