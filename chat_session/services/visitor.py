from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404

from chatbot.models import Chatbot, ChatbotAllowedOrigin
from chatbot.utils.choices import ChatbotStatusTypes
from chatbot.utils.validation import normalize_widget_origin
from chat_session.models import ChatMessage, ChatSession
from chat_session.services.visitor_tokens import (
    InvalidConversationToken,
    decode_conversation_token,
)
from chat_session.utils.choices import (
    ChatMessageSenderType,
    ChatSessionChannel,
    ChatSessionStatus,
)


PUBLIC_CHATBOT_EXCLUDED_STATUSES = (
    ChatbotStatusTypes.DISABLED,
    ChatbotStatusTypes.DISABLED_BY_ADMIN,
)
RESUMABLE_SESSION_STATUSES = (
    ChatSessionStatus.OPEN,
)


def get_public_chatbot(public_key):
    return get_object_or_404(
        Chatbot.objects.select_related("workspace", "widget_settings")
        .filter(
            is_deleted=False,
            workspace__is_active=True,
            widget_settings__is_enabled=True,
        )
        .exclude(status__in=PUBLIC_CHATBOT_EXCLUDED_STATUSES),
        widget_settings__public_key=public_key,
    )


def require_allowed_widget_origin(chatbot, origin):
    configured_origins = ChatbotAllowedOrigin.objects.filter(chatbot=chatbot)
    if not configured_origins.exists():
        return
    if not origin:
        raise PermissionDenied("An allowed Origin header is required.")
    try:
        normalized_origin = normalize_widget_origin(origin)
    except ValidationError as exc:
        raise PermissionDenied("The widget origin is not allowed.") from exc
    if not configured_origins.filter(
        origin=normalized_origin,
        is_active=True,
    ).exists():
        raise PermissionDenied("The widget origin is not allowed.")


def create_or_resume_conversation(
    chatbot,
    *,
    conversation_token="",
    user_metadata=None,
    metadata=None,
):
    session = None
    resumed = False
    if conversation_token:
        payload = decode_conversation_token(conversation_token)
        if payload["chatbot_id"] != str(chatbot.id):
            raise InvalidConversationToken(
                "The conversation token does not belong to this chatbot."
            )
        session = ChatSession.objects.filter(
            pk=payload["session_id"],
            chatbot=chatbot,
            visitor_id=payload["visitor_id"],
            channel=ChatSessionChannel.WEB_WIDGET,
            status__in=RESUMABLE_SESSION_STATUSES,
        ).first()
        resumed = session is not None

    if session is None:
        session = ChatSession.objects.create(
            chatbot=chatbot,
            channel=ChatSessionChannel.WEB_WIDGET,
            visitor_id=uuid4().hex,
            ai_enabled=chatbot.ai_enabled,
            user_metadata=user_metadata or {},
            metadata=metadata or {},
        )
    elif user_metadata is not None or metadata is not None:
        update_fields = ["updated_at"]
        if user_metadata is not None:
            session.user_metadata = user_metadata
            update_fields.append("user_metadata")
        if metadata is not None:
            session.metadata = metadata
            update_fields.append("metadata")
        session.save(update_fields=update_fields)
    return session, resumed


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
        pk=chat_session.pk
    )
    if chat_session.status not in RESUMABLE_SESSION_STATUSES:
        raise ValidationError("Cannot send a message to an ended conversation.")

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
