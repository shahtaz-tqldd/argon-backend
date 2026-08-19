from django.shortcuts import get_object_or_404
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from app.utils.response import APIResponse
from chatbot.api.v1.client.serializers import (
    AcceptChatbotInvitationSerializer,
    ChatbotInvitationSerializer,
    ChatbotMemberSerializer,
    ChatbotSerializer,
    InviteChatbotMemberSerializer,
)
from chatbot.models import Chatbot, ChatbotUser
from chatbot.permissions import IsChatbotUser
from chatbot.utils.choices import ChatbotStatusTypes


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


class ChatbotObjectMixin:
    _chatbot = None

    def get_chatbot(self):
        if self._chatbot is None:
            self._chatbot = get_object_or_404(
                Chatbot.objects.select_related("workspace").filter(
                    is_active=True,
                    is_deleted=False,
                    workspace__is_active=True,
                ),
                slug=self.kwargs["chatbot_slug"],
            )
            self.check_object_permissions(self.request, self._chatbot)
        return self._chatbot


class ChatbotListCreateView(GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotSerializer

    def get_queryset(self):
        return (
            Chatbot.objects.select_related("workspace")
            .filter(
                memberships__user=self.request.user,
                memberships__is_active=True,
                workspace__memberships__user=self.request.user,
                workspace__memberships__is_active=True,
                workspace__is_active=True,
                is_active=True,
                is_deleted=False,
            )
            .distinct()
            .order_by("-created_at")
        )

    def get(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return APIResponse.success(
            data=serializer.data,
            message="Chatbots fetched successfully.",
        )

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Chatbot creation failed.",
            )
        try:
            chatbot = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(
                exc.detail,
                "Chatbot creation failed.",
            )
        return APIResponse.success(
            data=self.get_serializer(chatbot).data,
            message="Chatbot created successfully.",
            status=status.HTTP_201_CREATED,
        )


class ChatbotDetailView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chatbot()).data,
            message="Chatbot fetched successfully.",
        )

    def _update(self, request, *, partial):
        chatbot = self.get_chatbot()
        serializer = self.get_serializer(
            chatbot,
            data=request.data,
            partial=partial,
        )
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Chatbot update failed.",
            )
        chatbot = serializer.save()
        return APIResponse.success(
            data=self.get_serializer(chatbot).data,
            message="Chatbot updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)

    def delete(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        chatbot.is_deleted = True
        chatbot.is_active = False
        chatbot.status = ChatbotStatusTypes.DISABLED
        chatbot.updated_by = request.user
        chatbot.save(
            update_fields=[
                "is_deleted",
                "is_active",
                "status",
                "updated_by",
                "updated_at",
            ]
        )
        return APIResponse.success(
            message="Chatbot deleted successfully.",
        )


class ChatbotMemberListView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotMemberSerializer

    def get(self, request, *args, **kwargs):
        memberships = ChatbotUser.objects.filter(
            chatbot=self.get_chatbot(),
            is_active=True,
            user__is_active=True,
        ).select_related("user")
        return APIResponse.success(
            data=self.get_serializer(memberships, many=True).data,
            message="Chatbot members fetched successfully.",
        )


class InviteChatbotMemberView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = InviteChatbotMemberSerializer
    allow_workspace_admin = True

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["chatbot"] = self.get_chatbot()
        return context

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Chatbot invitation failed.",
            )
        try:
            invitation = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(
                exc.detail,
                "Chatbot invitation failed.",
            )
        return APIResponse.success(
            data=ChatbotInvitationSerializer(invitation).data,
            message="Chatbot invitation sent successfully.",
            status=status.HTTP_201_CREATED,
        )


class AcceptChatbotInvitationView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = AcceptChatbotInvitationSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Invitation acceptance failed.",
            )
        try:
            membership = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(
                exc.detail,
                "Invitation acceptance failed.",
            )
        return APIResponse.success(
            data={
                "chatbot": ChatbotSerializer(
                    membership.chatbot,
                    context={"request": request},
                ).data,
                "membership": ChatbotMemberSerializer(membership).data,
            },
            message="Chatbot invitation accepted successfully.",
            status=status.HTTP_201_CREATED,
        )
