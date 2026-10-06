from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from app.utils.http import get_client_ip
from app.utils.response import APIResponse
from chatbot.api.v1.public.serializers import (
    PublicChatbotSerializer,
    PublicVisitorCreateSerializer,
    PublicVisitorQuerySerializer,
    PublicVisitorSerializer,
)
from chat.services.chat_public import (
    build_public_visitor_details,
    create_public_visitor,
    get_public_visitor_details,
)
from chat.services.visitor_tokens import issue_conversation_token
from chat.tasks import is_ai_reply_enabled
from chatbot.services.chatbot_public import (
    get_public_chatbot,
    require_allowed_widget_origin,
)


class PublicChatbotConfigAPIView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicChatbotSerializer

    def get(self, request, public_key, *args, **kwargs):
        chatbot = get_public_chatbot(public_key)
        return APIResponse.success(
            data=self.get_serializer(chatbot).data,
            message="Chatbot widget configuration fetched successfully.",
        )


class PublicVisitorDetailsAPIView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicVisitorSerializer

    def get(self, request, public_key, *args, **kwargs):
        query_serializer = PublicVisitorQuerySerializer(
            data=request.query_params
        )
        if not query_serializer.is_valid():
            return APIResponse.error(
                errors=query_serializer.errors,
                message="Visitor details could not be fetched.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        chatbot = get_public_chatbot(public_key)
        require_allowed_widget_origin(
            chatbot,
            request.headers.get("Origin", ""),
        )
        visitor = get_public_visitor_details(
            chatbot,
            query_serializer.validated_data["visitor_id"],
        )
        return APIResponse.success(
            data=self.get_serializer(visitor).data,
            message="Visitor details fetched successfully.",
        )


class PublicVisitorCreateAPIView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicVisitorCreateSerializer

    def post(self, request, public_key, *args, **kwargs):
        query_serializer = PublicVisitorQuerySerializer(
            data=request.query_params
        )
        if not query_serializer.is_valid():
            return APIResponse.error(
                errors=query_serializer.errors,
                message="Visitor could not be created.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return APIResponse.error(
                errors=serializer.errors,
                message="Visitor could not be created.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        chatbot = get_public_chatbot(public_key)
        require_allowed_widget_origin(
            chatbot,
            request.headers.get("Origin", ""),
        )
        visitor_id = query_serializer.validated_data["visitor_id"]
        try:
            chat_session, visitor_created, session_created = (
                create_public_visitor(
                    chatbot,
                    visitor_id,
                    ip_address=get_client_ip(request),
                    **serializer.validated_data,
                )
            )
        except DjangoValidationError as exc:
            errors = getattr(
                exc,
                "message_dict",
                {"non_field_errors": exc.messages},
            )
            return APIResponse.error(
                errors=errors,
                message=next(
                    iter(exc.messages),
                    "Visitor could not be created.",
                ),
                status=status.HTTP_400_BAD_REQUEST,
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
        visitor = build_public_visitor_details(chat_session.visitor)
        return APIResponse.success(
            data={
                "visitor": PublicVisitorSerializer(visitor).data,
                "session": {
                    "id": str(chat_session.id),
                    "visitor_id": visitor["visitor_id"],
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
                "visitor_created": visitor_created,
                "session_created": session_created,
            },
            message=(
                "Visitor created successfully."
                if visitor_created
                else (
                    "Visitor session initiated successfully."
                    if session_created
                    else "Visitor already exists."
                )
            ),
            status=(
                status.HTTP_201_CREATED
                if session_created
                else status.HTTP_200_OK
            ),
        )
