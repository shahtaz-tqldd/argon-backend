from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404

from chatbot.models import Chatbot, ChatbotAllowedOrigin
from chatbot.utils.choices import ChatbotStatusTypes
from chatbot.utils.validation import normalize_widget_origin
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
from lead_capture.models import Lead, LeadCaptureConfig


PUBLIC_CHATBOT_EXCLUDED_STATUSES = (
    ChatbotStatusTypes.DISABLED,
    ChatbotStatusTypes.DISABLED_BY_ADMIN,
)
RESUMABLE_SESSION_STATUSES = (
    ChatSessionStatus.OPEN,
)


def get_public_chatbot(public_key):
    return get_object_or_404(
        Chatbot.objects.select_related(
            "workspace",
            "widget_settings",
            "lead_capture_config",
            "appointment_booking_config",
        )
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
        .exclude(metadata__visibility="internal")
        .order_by("-created_at", "-id")
    )
    return sessions.select_related("lead", "chatbot").annotate(
        last_message_sender_type=Subquery(
            last_message.values("sender_type")[:1]
        ),
        last_message_content=Subquery(last_message.values("content")[:1]),
        last_message_agent_name=Subquery(
            last_message.values("sender__user__name")[:1]
        ),
    )


def _resolve_conversation_lead(chatbot, *, lead_id=None, lead_data=None):
    if lead_id is not None:
        try:
            return Lead.objects.get(pk=lead_id, chatbot=chatbot)
        except Lead.DoesNotExist as exc:
            raise ValidationError(
                {"lead_id": "The lead does not belong to this chatbot."}
            ) from exc

    if lead_data is None:
        return None
    try:
        config = chatbot.lead_capture_config
    except LeadCaptureConfig.DoesNotExist as exc:
        raise ValidationError(
            {"lead_data": "Lead collection is not enabled."}
        ) from exc
    if not config.is_enabled:
        raise ValidationError({"lead_data": "Lead collection is not enabled."})

    lead = Lead(
        chatbot=chatbot,
        collected_fields=lead_data,
        source="web_widget",
    )
    try:
        lead.full_clean()
    except ValidationError as exc:
        messages = getattr(exc, "message_dict", {}).get(
            "collected_fields",
            exc.messages,
        )
        raise ValidationError({"lead_data": messages}) from exc
    lead.save()
    return lead


@transaction.atomic
def create_or_resume_conversation(
    chatbot,
    *,
    conversation_token="",
    user_metadata=None,
    metadata=None,
    lead_id=None,
    lead_data=None,
):
    session = None
    resumed = False
    visitor_id = uuid4().hex
    if conversation_token:
        payload = decode_conversation_token(conversation_token)
        if payload["chatbot_id"] != str(chatbot.id):
            raise InvalidConversationToken(
                "The conversation token does not belong to this chatbot."
            )
        visitor_id = payload["visitor_id"]
        session = ChatSession.objects.filter(
            pk=payload["session_id"],
            chatbot=chatbot,
            visitor_id=payload["visitor_id"],
            channel=ChatSessionChannel.WEB_WIDGET,
            is_test=False,
            status__in=RESUMABLE_SESSION_STATUSES,
        ).first()
        resumed = session is not None

    lead = _resolve_conversation_lead(
        chatbot,
        lead_id=lead_id,
        lead_data=lead_data,
    )
    if session is None:
        session = ChatSession.objects.create(
            chatbot=chatbot,
            channel=ChatSessionChannel.WEB_WIDGET,
            visitor_id=visitor_id,
            lead=lead,
            ai_enabled=chatbot.ai_enabled,
            user_metadata=user_metadata or {},
            metadata=metadata or {},
        )
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
    elif (
        user_metadata is not None
        or metadata is not None
        or lead is not None
    ):
        update_fields = ["updated_at"]
        if user_metadata is not None:
            session.user_metadata = user_metadata
            update_fields.append("user_metadata")
        if metadata is not None:
            session.metadata = metadata
            update_fields.append("metadata")
        if lead is not None:
            session.lead = lead
            update_fields.append("lead")
        session.full_clean()
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
