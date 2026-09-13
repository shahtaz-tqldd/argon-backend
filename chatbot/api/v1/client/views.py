from app.utils.logger import logger
from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated

from agent.client import AgentClient
from analytics.choices import AIUsageType
from analytics.services.ai_usage import record_ai_usage
from app.services.r2 import schedule_delete_image
from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from appointment_booking.api.v1.client.serializers import (
    VisitorAppointmentCreateSerializer,
    VisitorAppointmentSerializer,
)
from appointment_booking.services import book_visitor_appointment
from chatbot.api.v1.client.serializers import (
    AcceptChatbotInvitationSerializer,
    ChatbotBaseResponseSerializer,
    ChatbotCreateSerializer,
    ChatbotDeleteSerializer,
    ChatbotDetailSerializer,
    ChatbotInvitationSerializer,
    ChatbotListQuerySerializer,
    ChatbotListSerializer,
    ChatbotMemberListSerializer,
    ChatbotMemberPermissionUpdateSerializer,
    ChatbotMemberQuerySerializer,
    ChatbotMemberSerializer,
    ChatbotQuerySerializer,
    ChatbotUpdateSerializer,
    ChatbotWidgetDetailSerializer,
    ChatbotWidgetUpdateSerializer,
    InviteChatbotMemberSerializer,
    PublicChatbotSerializer,
)
from chatbot.models import Chatbot, ChatbotInvitation, ChatbotUser
from chat.api.v1.client.serializers import (
    PublicVisitorSerializer,
    PublicVisitorSessionSerializer,
    VisitorConversationCreateSerializer,
    VisitorMessageCreateSerializer,
    VisitorMessageSerializer,
)
from chat.models import ChatMessage
from chat.services.events import publish_session_event
from chat.services.visitor import (
    create_or_resume_conversation,
    get_public_chatbot,
    get_public_visitor_details,
    get_public_visitor_sessions,
    get_visitor_chat_session,
    require_allowed_widget_origin,
    send_visitor_message,
)
from chat.services.visitor_tokens import (
    InvalidConversationToken,
    issue_conversation_token,
)
from chat.tasks import dispatch_ai_reply, is_ai_reply_enabled
from chatbot.utils.choices import (
    ChatbotPermissionTypes,
    ChatbotRoleTypes,
    ChatbotStatusTypes,
)
from chatbot.utils.permissions import available_chatbot_permissions
from subscription.models import ChatbotSubscription
from subscription.services.subscriptions import OPEN_SUBSCRIPTION_STATUSES
from workspace.models import WorkspaceRole

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
        query_serializer = ChatbotListQuerySerializer(
            data=self.request.query_params,
        )
        query_serializer.is_valid(raise_exception=True)
        queryset = (
            Chatbot.objects.select_related(
                "workspace",
                "created_by__profile",
            )
            .prefetch_related(
                Prefetch(
                    "memberships",
                    queryset=ChatbotUser.objects.filter(
                        is_active=True,
                        user__is_active=True,
                    )
                    .select_related("user__profile")
                    .order_by("user__email"),
                    to_attr="active_memberships",
                ),
                Prefetch(
                    "subscriptions",
                    queryset=ChatbotSubscription.objects.filter(
                        status__in=OPEN_SUBSCRIPTION_STATUSES,
                    ).order_by("-created_at"),
                    to_attr="open_subscriptions",
                ),
            )
            .filter(
                Q(
                    workspace__memberships__user=self.request.user,
                    workspace__memberships__is_active=True,
                    workspace__memberships__role=WorkspaceRole.ADMIN,
                )
                | Q(
                    memberships__user=self.request.user,
                    memberships__is_active=True,
                ),
                workspace__is_active=True,
                is_deleted=False,
            )
            .distinct()
            .order_by("-created_at")
        )
        workspace_slug = query_serializer.validated_data.get("workspace")
        if workspace_slug:
            queryset = queryset.filter(workspace__slug=workspace_slug)
        return queryset

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
    allow_workspace_admin = True

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chatbot()).data,
            message="Chatbot fetched successfully.",
        )


class ChatbotBaseAPIView(ChatbotObjectMixin, GenericAPIView):
    """Return chatbot identity and its subscription-backed capabilities."""

    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotBaseResponseSerializer
    allow_workspace_admin = True

    def get_chatbot(self):
        if self._chatbot is None:
            query_serializer = ChatbotQuerySerializer(
                data=self.request.query_params,
            )
            query_serializer.is_valid(raise_exception=True)
            self._chatbot = get_object_or_404(
                Chatbot.objects.select_related("workspace", "capacity")
                .prefetch_related(
                    Prefetch(
                        "subscriptions",
                        queryset=ChatbotSubscription.objects.filter(
                            status__in=OPEN_SUBSCRIPTION_STATUSES,
                        ),
                        to_attr="open_subscriptions",
                    )
                )
                .filter(
                    is_deleted=False,
                    workspace__is_active=True,
                ),
                slug=query_serializer.validated_data["chatbot"],
            )
            self.check_object_permissions(self.request, self._chatbot)
        return self._chatbot

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chatbot()).data,
            message="Chatbot fetched successfully.",
        )


class ChatbotWidgetDetailView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotWidgetDetailSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chatbot()).data,
            message="Chatbot widget details fetched successfully.",
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


class VisitorConversationView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = VisitorConversationCreateSerializer

    def post(self, request, public_key, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Conversation could not be started.",
            )
        chatbot = get_public_chatbot(public_key)
        require_allowed_widget_origin(
            chatbot,
            request.headers.get("Origin", ""),
        )
        try:
            chat_session, resumed = create_or_resume_conversation(
                chatbot,
                **serializer.validated_data,
            )
        except InvalidConversationToken as exc:
            return APIResponse.error(
                errors={"conversation_token": [str(exc)]},
                message=str(exc),
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except DjangoValidationError as exc:
            errors = getattr(exc, "message_dict", {"lead_data": exc.messages})
            return validation_error_response(
                errors,
                "Conversation could not be started.",
            )

        messages = list(
            ChatMessage.objects.filter(chat_session=chat_session)
            .select_related("sender__user__profile")
            .prefetch_related("attachments")
            .order_by("-created_at")[:50]
        )
        messages.reverse()
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
                "resumed": resumed,
                "messages": VisitorMessageSerializer(
                    messages,
                    many=True,
                ).data,
            },
            message=(
                "Conversation resumed successfully."
                if resumed
                else "Conversation created successfully."
            ),
            status=(status.HTTP_200_OK if resumed else status.HTTP_201_CREATED),
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


class PublicVisitorSessionListView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicVisitorSessionSerializer

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
            return validation_error_response(
                serializer.errors,
                "Appointment could not be booked.",
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


class ChatbotWidgetUpdateView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotWidgetUpdateSerializer
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
                "Chatbot widget update failed.",
            )
        try:
            chatbot = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(
                exc.detail,
                "Chatbot widget update failed.",
            )
        return APIResponse.success(
            data=ChatbotWidgetDetailSerializer(
                chatbot,
                context=self.get_serializer_context(),
            ).data,
            message="Chatbot widget updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


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
    permission_classes = [IsAuthenticated]
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
