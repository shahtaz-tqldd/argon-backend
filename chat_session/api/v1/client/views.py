from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import GenericAPIView

from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotPermissionTypes
from chat_session.api.v1.client.serializers import (
    AgentMessageCreateSerializer,
    ChatMessageSerializer,
    ChatSessionListSerializer,
    ChatSessionListQuerySerializer,
    ChatSessionObjectQuerySerializer,
    ChatSessionSerializer,
    ChatSessionTakeoverSerializer,
    ReassignSessionSerializer,
    ResolveSessionSerializer,
)
from chat_session.models import ChatMessage, ChatSession
from chat_session.services.messages import send_agent_message
from chat_session.services.takeover import (
    reassign_session,
    release_session,
    reopen_session,
    resolve_session,
    take_over_session,
)
from chat_session.utils.choices import (
    ChatMessageSenderType,
    ChatMessageStatus,
)


def validation_error_response(exc):
    errors = getattr(exc, "message_dict", None) or {
        "non_field_errors": exc.messages
    }
    return APIResponse.error(
        errors=errors,
        message=next(iter(exc.messages), "Request failed."),
        status=status.HTTP_400_BAD_REQUEST,
    )


class PaginatedChatSessionMixin:
    pagination_class = CustomPagination

    def paginated_response(self, queryset, *, message):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, self.request, view=self)
        return APIResponse.success(
            data=self.get_serializer(page, many=True).data,
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


class ChatSessionChatbotMixin:
    query_serializer_class = ChatSessionObjectQuerySerializer
    _query = None
    _chatbot = None
    _chatbot_user = None

    def get_query(self):
        if self._query is None:
            serializer = self.query_serializer_class(data=self.request.query_params)
            serializer.is_valid(raise_exception=True)
            self._query = serializer.validated_data
        return self._query

    def get_chatbot(self):
        if self._chatbot is None:
            self._chatbot = get_object_or_404(
                Chatbot.objects.select_related("workspace"),
                slug=self.get_query()["chatbot_slug"],
                is_deleted=False,
                workspace__is_active=True,
            )
            self.check_object_permissions(self.request, self._chatbot)
        return self._chatbot

    def get_chatbot_user(self):
        if self._chatbot_user is None:
            self._chatbot_user = get_object_or_404(
                ChatbotUser.objects.select_related("chatbot", "user"),
                chatbot=self.get_chatbot(),
                user=self.request.user,
                user__is_active=True,
                is_active=True,
            )
        return self._chatbot_user


class ChatSessionObjectMixin(ChatSessionChatbotMixin):
    _chat_session = None

    def get_chat_session(self):
        if self._chat_session is None:
            self._chat_session = get_object_or_404(
                ChatSession.objects.select_related(
                    "chatbot__workspace",
                    "lead",
                    "assigned_to__user",
                ),
                pk=self.get_query()["session_id"],
                chatbot=self.get_chatbot(),
            )
            self.check_object_permissions(self.request, self._chat_session)
        return self._chat_session


class ChatSessionListView(
    ChatSessionChatbotMixin,
    PaginatedChatSessionMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    query_serializer_class = ChatSessionListQuerySerializer
    serializer_class = ChatSessionListSerializer

    def get(self, request, *args, **kwargs):
        query = self.get_query()
        last_message = ChatMessage.objects.filter(
            chat_session=OuterRef("pk")
        ).order_by("-created_at", "-id")
        queryset = (
            ChatSession.objects.filter(chatbot=self.get_chatbot())
            .select_related("lead", "assigned_to__user")
            .annotate(
                unread_message_count=Count(
                    "messages",
                    filter=(
                        Q(messages__sender_type=ChatMessageSenderType.VISITOR)
                        & ~Q(messages__status=ChatMessageStatus.READ)
                    ),
                ),
                last_message_sender=Subquery(
                    last_message.values("sender_type")[:1]
                ),
                last_message_content=Subquery(
                    last_message.values("content")[:1]
                ),
            )
        )
        if query.get("status"):
            queryset = queryset.filter(status=query["status"])
        assignment = query.get("assignment", "all")
        if assignment == "mine":
            queryset = queryset.filter(assigned_to=self.get_chatbot_user())
        elif assignment == "assigned":
            queryset = queryset.filter(assigned_to__isnull=False)
        elif assignment == "unassigned":
            queryset = queryset.filter(assigned_to__isnull=True)
        return self.paginated_response(
            queryset,
            message="Chat sessions fetched successfully.",
        )


class ChatSessionDetailView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    serializer_class = ChatSessionSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chat_session()).data,
            message="Chat session fetched successfully.",
        )


class ChatMessageListView(
    ChatSessionObjectMixin,
    PaginatedChatSessionMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    serializer_class = ChatMessageSerializer

    def get(self, request, *args, **kwargs):
        queryset = (
            ChatMessage.objects.filter(chat_session=self.get_chat_session())
            .select_related("sender__user")
            .prefetch_related("attachments")
        )
        return self.paginated_response(
            queryset,
            message="Chat messages fetched successfully.",
        )


class AgentMessageCreateView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    serializer_class = AgentMessageCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            message = send_agent_message(
                self.get_chat_session(),
                self.get_chatbot_user(),
                **serializer.validated_data,
            )
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        return APIResponse.success(
            data=ChatMessageSerializer(message).data,
            message="Message sent successfully.",
            status=status.HTTP_201_CREATED,
        )


class TakeOverSessionView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT

    def post(self, request, *args, **kwargs):
        try:
            takeover = take_over_session(
                self.get_chat_session(),
                self.get_chatbot_user(),
            )
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        return APIResponse.success(
            data=ChatSessionTakeoverSerializer(takeover).data,
            message="Chat session taken over successfully.",
            status=status.HTTP_201_CREATED,
        )


class ReassignSessionView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    serializer_class = ReassignSessionSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_agent = get_object_or_404(
            ChatbotUser.objects.select_related("user"),
            pk=serializer.validated_data["agent_id"],
            chatbot=self.get_chatbot(),
            user__is_active=True,
            is_active=True,
        )
        try:
            takeover = reassign_session(self.get_chat_session(), new_agent)
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        return APIResponse.success(
            data=ChatSessionTakeoverSerializer(takeover).data,
            message="Chat session reassigned successfully.",
            status=status.HTTP_201_CREATED,
        )


class ReleaseSessionView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT

    def post(self, request, *args, **kwargs):
        try:
            takeover = release_session(self.get_chat_session())
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        return APIResponse.success(
            data=ChatSessionTakeoverSerializer(takeover).data,
            message="Chat session released successfully.",
        )


class ResolveSessionView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    serializer_class = ResolveSessionSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            takeover = resolve_session(
                self.get_chat_session(),
                **serializer.validated_data,
            )
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        return APIResponse.success(
            data=ChatSessionTakeoverSerializer(takeover).data,
            message="Chat session resolved successfully.",
        )


class ReopenSessionView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT

    def post(self, request, *args, **kwargs):
        try:
            takeover = reopen_session(
                self.get_chat_session(),
                self.get_chatbot_user(),
            )
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        return APIResponse.success(
            data=ChatSessionTakeoverSerializer(takeover).data,
            message="Chat session reopened successfully.",
        )
