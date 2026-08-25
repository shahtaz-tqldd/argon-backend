from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from accounts.api.v1.client.serializers import build_auth_token_payload
from app.services.r2 import schedule_delete_image
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
    ChatbotMemberListSerializer,
    ChatbotMemberPermissionUpdateSerializer,
    ChatbotMemberQuerySerializer,
    ChatbotMemberSerializer,
    ChatbotQuerySerializer,
    ChatbotShortDetailSerializer,
    ChatbotUpdateSerializer,
    InviteChatbotMemberSerializer,
)
from chatbot.models import Chatbot, ChatbotInvitation, ChatbotUser
from chatbot.utils.choices import (
    ChatbotPermissionTypes,
    ChatbotRoleTypes,
    ChatbotStatusTypes,
)
from chatbot.utils.permissions import available_chatbot_permissions

User = get_user_model()


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
                ChatbotUser.objects.select_related(
                    "chatbot",
                    "user__profile",
                ),
                chatbot=self.get_chatbot(),
                user__email__iexact=(
                    query_serializer.validated_data["member_email"]
                ),
                user__is_active=True,
                is_active=True,
            )
            invitation = ChatbotInvitation.objects.filter(
                chatbot=self._chatbot_member.chatbot,
                email__iexact=self._chatbot_member.user.email,
            ).first()
            self._chatbot_member.invited_at = (
                invitation.invited_at
                if invitation is not None
                else self._chatbot_member.created_at
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
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION

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
        previous_logo_url = chatbot.logo
        chatbot.is_deleted = True
        chatbot.logo = ""
        chatbot.status = ChatbotStatusTypes.DISABLED
        chatbot.updated_by = request.user
        chatbot.save(
            update_fields=[
                "is_deleted",
                "logo",
                "status",
                "updated_by",
                "updated_at",
            ]
        )
        schedule_delete_image(image_url=previous_logo_url)
        return APIResponse.success(
            message="Chatbot deleted successfully.",
        )


class ChatbotMemberListView(
    ChatbotObjectMixin,
    PaginatedListMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotMemberListSerializer

    def get_records(self):
        chatbot = self.get_chatbot()
        memberships = list(
            ChatbotUser.objects.filter(
                chatbot=chatbot,
                is_active=True,
                user__is_active=True,
            ).select_related("chatbot", "user__profile")
        )
        invitations = list(
            ChatbotInvitation.objects.filter(
                chatbot=chatbot,
                accepted_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).order_by("-invited_at")
        )

        all_invitations = {
            invitation.email.casefold(): invitation
            for invitation in ChatbotInvitation.objects.filter(chatbot=chatbot)
        }
        active_emails = {
            membership.user.email.casefold() for membership in memberships
        }
        for membership in memberships:
            invitation = all_invitations.get(membership.user.email.casefold())
            membership.invited_at = (
                invitation.invited_at
                if invitation is not None
                else membership.created_at
            )

        pending_invitations = [
            invitation
            for invitation in invitations
            if invitation.email.casefold() not in active_emails
        ]
        invited_users = {
            user.email.casefold(): user
            for user in User.objects.filter(
                email__in=[item.email for item in pending_invitations],
            ).select_related("profile")
        }
        for invitation in pending_invitations:
            invitation.user = invited_users.get(
                invitation.email.casefold(),
                SimpleNamespace(
                    email=invitation.email,
                    name="",
                    profile=SimpleNamespace(avatar_url=""),
                    last_active=None,
                    last_login=None,
                ),
            )
            invitation.role = ChatbotRoleTypes.MEMBER
            invitation.all_permissions = False
            invitation.is_active = False

        return sorted(
            [*memberships, *pending_invitations],
            key=lambda record: (record.invited_at, str(record.id)),
            reverse=True,
        )

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_records(),
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


class ChatbotMemberPermissionView(ChatbotMemberObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotMemberPermissionUpdateSerializer
    chatbot_admin_only = True

    def permission_data(self, membership):
        return {
            "member": ChatbotMemberSerializer(
                membership,
                context=self.get_serializer_context(),
            ).data,
            "available_permissions": available_chatbot_permissions(
                membership.chatbot,
            ),
        }

    def get(self, request, *args, **kwargs):
        membership = self.get_chatbot_member()
        return APIResponse.success(
            data=self.permission_data(membership),
            message="Chatbot member permissions fetched successfully.",
        )

    def patch(self, request, *args, **kwargs):
        membership = self.get_chatbot_member()
        serializer = self.get_serializer(
            membership,
            data=request.data,
        )
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Chatbot member permission update failed.",
            )
        membership = serializer.save()
        return APIResponse.success(
            data=self.permission_data(membership),
            message="Chatbot member permissions updated successfully.",
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
        serializer_data = request.data.copy()
        member_email = request.query_params.get("member_email")
        if member_email is not None:
            serializer_data["email"] = member_email
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
        auth_tokens = build_auth_token_payload(membership.user)
        return APIResponse.success(
            data={
                **auth_tokens,
                "chatbot": ChatbotDetailSerializer(
                    membership.chatbot,
                    context={"request": request},
                ).data,
                "membership": ChatbotMemberSerializer(membership).data,
            },
            message="Chatbot invitation accepted successfully.",
            status=status.HTTP_201_CREATED,
        )
