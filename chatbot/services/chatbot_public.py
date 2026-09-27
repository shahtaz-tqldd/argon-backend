from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404

from chatbot.models import Chatbot, ChatbotAllowedOrigin
from chatbot.utils.choices import ChatbotStatusTypes
from chatbot.utils.validation import normalize_widget_origin

PUBLIC_CHATBOT_EXCLUDED_STATUSES = (
    ChatbotStatusTypes.DISABLED,
    ChatbotStatusTypes.DISABLED_BY_ADMIN,
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
