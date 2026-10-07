from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import Http404

from chat.models import ChatMessage, ChatSession
from chat.services.events import publish_session_event
from chat.services.messages import create_chat_message
from chat.services.visitor_tokens import decode_conversation_token
from chat.utils.choices import (
    ChatMessageSenderType,
    ChatSessionChannel,
    ChatSessionStatus,
)
from chatbot.services.visitors import (
    get_or_create_visitor,
    get_visitor,
    get_visitor_by_pk,
    update_visitor_profile,
)
from lead_capture.models import Lead, LeadCaptureConfig


RESUMABLE_SESSION_STATUSES = (
    ChatSessionStatus.OPEN,
)


def _visitor_sessions(chatbot, visitor):
    """All web-widget sessions of a visitor. The visitor's lead is 1:1, so
    lead-linked sessions are exactly this visitor's sessions."""
    return ChatSession.objects.filter(
        visitor=visitor,
        chatbot=chatbot,
        channel=ChatSessionChannel.WEB_WIDGET,
        is_test=False,
    )


def _get_visitor_or_404(chatbot, visitor_id):
    visitor = get_visitor(chatbot, visitor_id)
    if visitor is None:
        raise Http404("Visitor not found.")
    return visitor


def build_public_visitor_details(visitor):
    lead = visitor.lead
    return {
        "visitor_id": visitor.visitor_id,
        "lead_id": lead.id if lead is not None else None,
        "lead_data": lead.collected_fields if lead is not None else {},
        "ip_address": visitor.ip_address,
        "detected_location": visitor.detected_location,
        "detected_country": visitor.detected_country,
        "user_metadata": visitor.metadata or {},
    }


def get_public_visitor_details(chatbot, visitor_id):
    visitor = _get_visitor_or_404(chatbot, visitor_id)
    return build_public_visitor_details(visitor)


def get_public_visitor_sessions(chatbot, visitor_id):
    visitor = _get_visitor_or_404(chatbot, visitor_id)
    last_message = (
        ChatMessage.objects.filter(chat_session=OuterRef("pk"))
        .exclude(metadata__contains={"visibility": "internal"})
        .order_by("-created_at", "-id")
    )
    return (
        _visitor_sessions(chatbot, visitor)
        .select_related("lead", "chatbot")
        .annotate(
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
        # Explicit ordering: the aggregate above adds a GROUP BY, which makes
        # Django drop ChatSession.Meta.ordering — without this the list comes
        # back in arbitrary DB order.
        .order_by("-last_activity_at", "-created_at", "-id")
    )


@transaction.atomic
def create_public_visitor_session(
    chatbot,
    visitor_id,
    *,
    ip_address=None,
    detected_location=None,
    detected_country=None,
    user_metadata=None,
    metadata=None,
    lead=None,
):
    visitor = get_or_create_visitor(chatbot, visitor_id)
    if visitor.is_blocked:
        raise ValidationError("This visitor has been blocked.")

    update_visitor_profile(
        visitor,
        ip_address=ip_address,
        detected_location=detected_location,
        detected_country=detected_country,
        metadata=user_metadata,
    )
    if lead is not None and visitor.lead_id != lead.pk:
        visitor.lead = lead
        visitor.save(update_fields=["lead", "updated_at"])

    session = ChatSession(
        chatbot=chatbot,
        channel=ChatSessionChannel.WEB_WIDGET,
        visitor=visitor,
        lead=lead if lead is not None else visitor.lead,
        ai_enabled=chatbot.ai_enabled,
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


@transaction.atomic
def create_public_visitor(
    chatbot,
    visitor_id,
    *,
    lead_data=None,
    ip_address=None,
    detected_location=None,
    detected_country=None,
    user_metadata=None,
    metadata=None,
):
    existing_visitor = get_visitor(chatbot, visitor_id)
    if existing_visitor is not None and existing_visitor.is_blocked:
        raise ValidationError("This visitor has been blocked.")
    visitor_created = existing_visitor is None

    lead_capture_enabled = (
        lead_data is not None
        and LeadCaptureConfig.objects.filter(
            chatbot=chatbot,
            is_enabled=True,
        ).exists()
    )
    if not lead_capture_enabled:
        active_session = (
            ChatSession.objects.filter(
                chatbot=chatbot,
                visitor=existing_visitor,
                channel=ChatSessionChannel.WEB_WIDGET,
                is_test=False,
                status__in=RESUMABLE_SESSION_STATUSES,
            )
            .select_related("lead", "visitor", "visitor__lead")
            .order_by("-last_activity_at", "-created_at")
            .first()
            if existing_visitor is not None
            else None
        )
        if active_session is not None:
            update_visitor_profile(
                existing_visitor,
                ip_address=ip_address,
                detected_location=detected_location,
                detected_country=detected_country,
                metadata=user_metadata,
            )
            return active_session, visitor_created, False
        lead = None
    else:
        if existing_visitor is not None and existing_visitor.lead_id:
            lead = existing_visitor.lead
            lead.collected_fields = lead_data
        else:
            lead = Lead(
                chatbot=chatbot,
                collected_fields=lead_data,
                source="web_widget",
            )
        lead.full_clean()
        lead.save()

    session = create_public_visitor_session(
        chatbot,
        visitor_id,
        ip_address=ip_address,
        detected_location=detected_location,
        detected_country=detected_country,
        user_metadata=user_metadata,
        metadata=metadata,
        lead=lead,
    )
    return session, visitor_created, True


def get_public_visitor_session(
    chatbot,
    visitor_id,
    session_id,
    conversation_token,
    *,
    resumable_only=False,
):
    payload = decode_conversation_token(conversation_token)
    visitor = _get_visitor_or_404(chatbot, visitor_id)
    if (
        payload["session_id"] != str(session_id)
        or payload["chatbot_id"] != str(chatbot.id)
        or payload["visitor_id"] != str(visitor.id)
    ):
        raise PermissionDenied(
            "The conversation token does not belong to this visitor session."
        )
    filters = {}
    if resumable_only:
        filters["status__in"] = RESUMABLE_SESSION_STATUSES
    try:
        return ChatSession.objects.select_related("lead", "visitor").get(
            pk=session_id,
            chatbot=chatbot,
            visitor=visitor,
            channel=ChatSessionChannel.WEB_WIDGET,
            is_test=False,
            **filters,
        )
    except ChatSession.DoesNotExist as exc:
        raise Http404("Conversation not found.") from exc


def get_visitor_chat_session(chatbot, session_id, conversation_token):
    """Return an open token-authenticated session for public integrations."""
    payload = decode_conversation_token(conversation_token)
    visitor = get_visitor_by_pk(chatbot, payload["visitor_id"])
    if visitor is None:
        raise Http404("Conversation not found.")
    return get_public_visitor_session(
        chatbot,
        visitor.visitor_id,
        session_id,
        conversation_token,
        resumable_only=True,
    )


def send_visitor_message(
    chat_session,
    *,
    content,
    metadata=None,
    external_id="",
    attachments=None,
):
    with transaction.atomic():
        chat_session = (
            ChatSession.objects.select_for_update(of=("self",))
            .select_related("visitor")
            .get(
                pk=chat_session.pk,
                is_test=False,
            )
        )
        if chat_session.status not in RESUMABLE_SESSION_STATUSES:
            raise ValidationError(
                "Cannot send a message to an ended conversation."
            )

        if chat_session.visitor is not None and chat_session.visitor.is_blocked:
            raise ValidationError(
                "This visitor has been blocked from sending messages."
            )

        duplicate = None
        if external_id:
            duplicate = ChatMessage.objects.filter(
                chat_session=chat_session,
                external_id=external_id,
                sender_type=ChatMessageSenderType.VISITOR,
            ).first()
        if duplicate is None:
            return (
                create_chat_message(
                    chat_session,
                    sender_type=ChatMessageSenderType.VISITOR,
                    content=content,
                    metadata=metadata,
                    external_id=external_id,
                    attachments=attachments,
                ),
                True,
            )
        # A duplicate keeps the original attachments.
        return duplicate, False
