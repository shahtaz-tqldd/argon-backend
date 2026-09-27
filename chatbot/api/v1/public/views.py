from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from app.utils.response import APIResponse

from chatbot.api.v1.public.serializers import (
    PublicChatbotSerializer,
    PublicVisitorSerializer,
)

from chat.services.chat_public import get_public_visitor_details
from chatbot.services.chatbot_public import (
    get_public_chatbot,
    require_allowed_widget_origin,
)


class PublicChatbotView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicChatbotSerializer

    def get(self, request, public_key, *args, **kwargs):
        chatbot = get_public_chatbot(public_key)
        return APIResponse.success(
            data=self.get_serializer(chatbot).data,
            message="Chatbot widget configuration fetched successfully.",
        )


class PublicVisitorDetailView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicVisitorSerializer

    def get(self, request, public_key, visitor_id, *args, **kwargs):
        chatbot = get_public_chatbot(public_key)
        require_allowed_widget_origin(
            chatbot,
            request.headers.get("Origin", ""),
        )
        visitor = get_public_visitor_details(chatbot, visitor_id)
        return APIResponse.success(
            data=self.get_serializer(visitor).data,
            message="Visitor details fetched successfully.",
        )
