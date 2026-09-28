from app.utils.logger import logger
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from app.utils.pagination import CustomPagination
from app.utils.response import APIResponse
from chat.api.v1.public.serializers import (
    PublicVisitorSessionSerializer,
    VisitorSessionCreateSerializer,
    VisitorConversationQuerySerializer,
    VisitorMessageCreateSerializer,
    VisitorMessageSerializer,
)
from chat.models import ChatMessage
from chat.services.events import publish_session_event
from chat.services.chat_public import (
    create_public_visitor_session,
    get_visitor_conversation,
    get_public_visitor_sessions,
    get_visitor_chat_session,
    send_visitor_message,
)
from chatbot.services.chatbot_public import (
    get_public_chatbot,
    require_allowed_widget_origin,
)
from chat.services.visitor_tokens import (
    InvalidConversationToken,
    issue_conversation_token,
)
from chat.tasks import dispatch_ai_reply, is_ai_reply_enabled


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


def validation_error_response(errors, fallback):
    return APIResponse.error(
        errors=errors,
        message=first_error_message(errors, fallback=fallback),
        status=status.HTTP_400_BAD_REQUEST,
    )


class PublicVisitorSessionListView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicVisitorSessionSerializer

    def get_serializer_class(self):
        if self.request.method == "POST":
            return VisitorSessionCreateSerializer
        return super().get_serializer_class()

    def post(self, request, public_key, visitor_id, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Visitor session could not be created.",
            )
        chatbot = get_public_chatbot(public_key)
        require_allowed_widget_origin(
            chatbot,
            request.headers.get("Origin", ""),
        )
        try:
            chat_session = create_public_visitor_session(
                chatbot,
                visitor_id,
                **serializer.validated_data,
            )
        except DjangoValidationError as exc:
            errors = getattr(
                exc,
                "message_dict",
                {"non_field_errors": exc.messages},
            )
            return validation_error_response(
                errors,
                "Visitor session could not be created.",
            )

        token = issue_conversation_token(chat_session)
        scheme = "wss" if request.is_secure() else "ws"
        websocket_base_url = settings.WIDGET_WEBSOCKET_BASE_URL.rstrip("/")
        if not websocket_base_url:
            websocket_base_url = f"{scheme}://{request.get_host()}"
        websocket_path = (
            f"/ws/widget/chatbots/{public_key}/"
            f"conversations/{chat_session.id}/"
        )
        return APIResponse.success(
            data={
                "session": {
                    "id": str(chat_session.id),
                    "visitor_id": chat_session.visitor_id,
                    "status": chat_session.status,
                    "ai_enabled": is_ai_reply_enabled(
                        chat_session,
                        chatbot,
                    ),
                },
                "conversation_token": token,
                "websocket_url": (
                    f"{websocket_base_url}{websocket_path}"
                    f"?{urlencode({'token': token})}"
                ),
            },
            message="Visitor session created successfully.",
            status=status.HTTP_201_CREATED,
        )

    def get(self, request, public_key, visitor_id, *args, **kwargs):
        chatbot = get_public_chatbot(public_key)
        require_allowed_widget_origin(
            chatbot,
            request.headers.get("Origin", ""),
        )
        sessions = get_public_visitor_sessions(chatbot, visitor_id)
        return APIResponse.success(
            data=self.get_serializer(sessions, many=True).data,
            message="Visitor sessions fetched successfully.",
        )


class VisitorConversationView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = VisitorConversationQuerySerializer
    pagination_class = CustomPagination

    def get(self, request, public_key, *args, **kwargs):
        serializer = self.get_serializer(data=request.query_params)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Conversation messages could not be fetched.",
            )
        chatbot = get_public_chatbot(public_key)
        require_allowed_widget_origin(
            chatbot,
            request.headers.get("Origin", ""),
        )
        try:
            chat_session = get_visitor_conversation(
                chatbot,
                serializer.validated_data["conversation_token"],
            )
        except InvalidConversationToken as exc:
            return APIResponse.error(
                errors={"conversation_token": [str(exc)]},
                message=str(exc),
                status=status.HTTP_401_UNAUTHORIZED,
            )

        message_queryset = (
            ChatMessage.objects.filter(chat_session=chat_session)
            .exclude(metadata__contains={"visibility": "internal"})
            .select_related("sender__user__profile")
            .prefetch_related("attachments")
            .order_by("-created_at", "-id")
        )
        paginator = self.pagination_class()
        messages = list(
            paginator.paginate_queryset(message_queryset, request, view=self)
        )
        messages.reverse()
        return APIResponse.success(
            data=VisitorMessageSerializer(messages, many=True).data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Conversation messages fetched successfully.",
        )


class VisitorMessageCreateView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = VisitorMessageCreateSerializer

    @staticmethod
    def _bearer_token(request):
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization.split(" ", 1)[1].strip()
        return ""

    def post(self, request, public_key, session_id, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Message could not be sent.",
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
            message, created = send_visitor_message(
                chat_session,
                content=serializer.validated_data["content"],
                metadata=serializer.validated_data.get("metadata"),
                external_id=serializer.validated_data.get(
                    "client_message_id",
                    "",
                ),
            )
        except DjangoValidationError as exc:
            return APIResponse.error(
                errors={"non_field_errors": exc.messages},
                message=next(iter(exc.messages), "Message could not be sent."),
                status=status.HTTP_409_CONFLICT,
            )
        ai_queued = False
        if created and is_ai_reply_enabled(chat_session, chatbot):
            try:
                dispatch_ai_reply(str(message.id))
                ai_queued = True
            except Exception:
                logger.exception(
                    "Could not queue AI reply for visitor message %s",
                    message.id,
                )
                publish_session_event(
                    chat_session.id,
                    chat_session.chatbot_id,
                    "ai.response.failed",
                    {"code": "queue_unavailable", "retryable": True},
                )
        return APIResponse.success(
            data={
                "message": VisitorMessageSerializer(message).data,
                "duplicate": not created,
                "ai_queued": ai_queued,
            },
            message=(
                "Message already accepted."
                if not created
                else "Message accepted successfully."
            ),
            status=(status.HTTP_200_OK if not created else status.HTTP_201_CREATED),
        )


