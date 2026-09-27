from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from agent.client import AgentClient
from analytics.choices import AIUsageType
from analytics.services.ai_usage import record_ai_usage
from app.utils.logger import logger
from app.utils.response import APIResponse
from appointment_booking.api.v1.public.serializers import (
    VisitorAppointmentCreateSerializer,
    VisitorAppointmentSerializer,
)
from appointment_booking.services import book_visitor_appointment
from chat.services.chat_public import get_visitor_chat_session
from chat.services.visitor_tokens import InvalidConversationToken
from chatbot.services.chatbot_public import (
    get_public_chatbot,
    require_allowed_widget_origin,
)


def first_error_message(errors, fallback="Request failed."):
    if isinstance(errors, dict):
        for value in errors.values():
            message = first_error_message(value, fallback="")
            if message:
                return message
        return fallback
    if isinstance(errors, (list, tuple)):
        for value in errors:
            message = first_error_message(value, fallback="")
            if message:
                return message
        return fallback
    return str(errors) if errors else fallback


class VisitorAppointmentCreateView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = VisitorAppointmentCreateSerializer

    @staticmethod
    def _bearer_token(request):
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization.split(" ", 1)[1].strip()
        return ""

    def post(self, request, public_key, session_id, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return APIResponse.error(
                errors=serializer.errors,
                message=first_error_message(
                    serializer.errors,
                    fallback="Appointment could not be booked.",
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        chatbot = get_public_chatbot(public_key)
        require_allowed_widget_origin(
            chatbot,
            request.headers.get("Origin", ""),
        )
        token = self._bearer_token(request)
        if not token:
            return APIResponse.error(
                message="A conversation bearer token is required.",
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            chat_session = get_visitor_chat_session(
                chatbot,
                session_id,
                token,
            )
        except InvalidConversationToken as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            appointment, created = book_visitor_appointment(
                chat_session,
                starts_at=serializer.validated_data["starts_at"],
                collected_fields=serializer.validated_data["collected_fields"],
            )
        except DjangoValidationError as exc:
            errors = getattr(
                exc,
                "message_dict",
                {"non_field_errors": exc.messages},
            )
            return APIResponse.error(
                errors=errors,
                message=first_error_message(
                    errors,
                    fallback="Appointment could not be booked.",
                ),
                status=status.HTTP_409_CONFLICT,
            )

        agent_response = None
        try:
            agent_response = AgentClient(chatbot, chat_session).confirm_booking_sync(
                appointment_id=str(appointment.id),
                user_id=chat_session.visitor_id or str(chat_session.id),
            )
            record_ai_usage(
                chatbot=chatbot,
                chat_session=chat_session,
                usage_type=AIUsageType.CHAT,
                cost=agent_response["cost"],
                token_usage=agent_response["token"],
                model=settings.GEMINI_CHAT_MODEL,
                metadata={
                    "event": "appointment_confirmation",
                    "appointment_id": str(appointment.id),
                },
            )
        except Exception:
            logger.exception(
                "Could not append appointment %s to agent session %s",
                appointment.id,
                chat_session.id,
            )

        return APIResponse.success(
            data={
                "appointment": VisitorAppointmentSerializer(appointment).data,
                "duplicate": not created,
                "agent_acknowledged": agent_response is not None,
                "agent_reply": (
                    agent_response["result"]["content"]
                    if agent_response is not None
                    else ""
                ),
            },
            message=(
                "Appointment already booked."
                if not created
                else "Appointment request submitted successfully."
            ),
            status=(status.HTTP_200_OK if not created else status.HTTP_201_CREATED),
        )
