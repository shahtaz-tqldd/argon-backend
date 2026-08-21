from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from chatbot.api.v1.client.serializers import (
    AcceptChatbotInvitationSerializer,
    ChatbotCreateSerializer,
    ChatbotDeleteSerializer,
    ChatbotDetailSerializer,
    ChatbotInvitationSerializer,
    ChatbotListSerializer,
    ChatbotMemberQuerySerializer,
    ChatbotMemberSerializer,
    ChatbotQuerySerializer,
    ChatbotShortDetailSerializer,
    ChatbotUpdateSerializer,
    InviteChatbotMemberSerializer,
)
from chatbot.models import Chatbot, ChatbotUser
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
            query_serializer = ChatbotQuerySerializer(
                data=self.request.query_params,
            )
            query_serializer.is_valid(raise_exception=True)
            self._chatbot = get_object_or_404(
                Chatbot.objects.select_related("workspace").filter(
                    is_deleted=False,
                    workspace__is_active=True,
                ),
                slug=query_serializer.validated_data["chatbot"],
            )
            self.check_object_permissions(self.request, self._chatbot)
        return self._chatbot


class PaginatedListMixin:
    pagination_class = CustomPagination

    def paginated_response(self, queryset, *, message):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, self.request, view=self)
        serializer = self.get_serializer(page, many=True)
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(self.request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message=message,
        )


class ChatbotMemberObjectMixin(ChatbotObjectMixin):
    _chatbot_member = None

    def get_chatbot_member(self):
        if self._chatbot_member is None:
            query_serializer = ChatbotMemberQuerySerializer(
                data=self.request.query_params,
            )
            query_serializer.is_valid(raise_exception=True)
            self._chatbot_member = get_object_or_404(
                ChatbotUser.objects.select_related("chatbot", "user"),
                chatbot=self.get_chatbot(),
                user__email__iexact=(
                    query_serializer.validated_data["member_email"]
                ),
                user__is_active=True,
                is_active=True,
            )
            self.check_object_permissions(self.request, self._chatbot_member)
        return self._chatbot_member


class ChatbotListView(PaginatedListMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotListSerializer

    def get_queryset(self):
        return (
            Chatbot.objects.select_related("workspace")
            .filter(
                memberships__user=self.request.user,
                memberships__is_active=True,
                workspace__memberships__user=self.request.user,
                workspace__memberships__is_active=True,
                workspace__is_active=True,
                is_deleted=False,
            )
            .distinct()
            .order_by("-created_at")
        )

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Chatbots fetched successfully.",
        )


class ChatbotCreateView(GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotCreateSerializer

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
    serializer_class = ChatbotDetailSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chatbot()).data,
            message="Chatbot fetched successfully.",
        )


class ChatbotShortDetailView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotShortDetailSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chatbot()).data,
            message="Chatbot fetched successfully.",
        )


class ChatbotUpdateView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotUpdateSerializer

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


class ChatbotDeleteView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotDeleteSerializer

    def delete(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        chatbot.is_deleted = True
        chatbot.status = ChatbotStatusTypes.DISABLED
        chatbot.updated_by = request.user
        chatbot.save(
            update_fields=[
                "is_deleted",
                "status",
                "updated_by",
                "updated_at",
            ]
        )
        return APIResponse.success(
            message="Chatbot deleted successfully.",
        )


class ChatbotMemberListView(
    ChatbotObjectMixin,
    PaginatedListMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotMemberSerializer

    def get(self, request, *args, **kwargs):
        memberships = ChatbotUser.objects.filter(
            chatbot=self.get_chatbot(),
            is_active=True,
            user__is_active=True,
        ).select_related("user")
        return self.paginated_response(
            memberships,
            message="Chatbot members fetched successfully.",
        )


class ChatbotMemberDetailView(ChatbotMemberObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotMemberSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chatbot_member()).data,
            message="Chatbot member fetched successfully.",
        )


class RemoveChatbotMemberView(ChatbotMemberObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotMemberSerializer

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        membership = self.get_chatbot_member()
        member = membership.user
        membership.delete()
        member.is_orphan = True
        member.save(update_fields=["is_orphan", "updated_at"])
        return APIResponse.success(
            data={"member_email": member.email},
            message="Chatbot member removed successfully.",
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
        serializer_data = request.data
        member_email = request.query_params.get("member_email")
        if member_email is not None:
            serializer_data = {"email": member_email}
        serializer = self.get_serializer(data=serializer_data)
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
                "chatbot": ChatbotDetailSerializer(
                    membership.chatbot,
                    context={"request": request},
                ).data,
                "membership": ChatbotMemberSerializer(membership).data,
            },
            message="Chatbot invitation accepted successfully.",
            status=status.HTTP_201_CREATED,
        )
