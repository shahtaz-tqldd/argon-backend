from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import GenericAPIView

from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotPermissionTypes
from chat.api.v1.client.serializers import (
    AgentMessageCreateSerializer,
    ChatMessageSerializer,
    ChatSessionListSerializer,
    ChatSessionListQuerySerializer,
    ChatSessionObjectQuerySerializer,
    ChatSessionQuerySerializer,
    ChatSessionSerializer,
    ChatSessionTakeoverSerializer,
    ChatSessionTransferListQuerySerializer,
    ChatSessionTransferObjectQuerySerializer,
    ChatSessionTransferSerializer,
    ResolveSessionSerializer,
    SessionOverviewQuerySerializer,
    TransferSessionSerializer,
)
from chat.models import ChatMessage, ChatSession, ChatSessionTransfer
from chat.services.messages import send_agent_message
from chat.services.takeover import (
    accept_transfer,
    cancel_transfer,
    decline_transfer,
    expire_pending_transfers,
    release_session,
    reopen_session,
    request_transfer,
    resolve_session,
    take_over_session,
)
from chat.utils.choices import (
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


def _aware_start(value):
    return timezone.make_aware(
        datetime.combine(value, time.min),
        timezone.get_current_timezone(),
    )


def _percentage_change(current_count, previous_count):
    if previous_count == 0:
        return 100.0 if current_count else 0.0
    return round(((current_count - previous_count) / previous_count) * 100, 2)


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
                ChatbotUser.objects.select_related(
                    "chatbot",
                    "user__profile",
                ),
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
                    "assigned_to__user__profile",
                ),
                pk=self.get_query()["session_id"],
                chatbot=self.get_chatbot(),
            )
            self.check_object_permissions(self.request, self._chat_session)
        return self._chat_session


class ChatSessionTransferObjectMixin(ChatSessionChatbotMixin):
    query_serializer_class = ChatSessionTransferObjectQuerySerializer
    _transfer = None

    def get_transfer(self):
        if self._transfer is None:
            self._transfer = get_object_or_404(
                ChatSessionTransfer.objects.select_related(
                    "chat_session",
                    "from_agent__user__profile",
                    "to_agent__user__profile",
                ),
                pk=self.get_query()["transfer_id"],
                chat_session__chatbot=self.get_chatbot(),
            )
        return self._transfer


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
            .select_related(
                "chatbot",
                "lead",
                "assigned_to__user__profile",
            )
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
                last_message_agent_name=Subquery(
                    last_message.values("sender__user__name")[:1]
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
        if query.get("requires_attention"):
            queryset = queryset.filter(requires_attention=True)
        if query.get("is_recently_active"):
            recently_active_since = timezone.now() - timedelta(minutes=10)
            queryset = queryset.filter(
                last_visitor_activity_at__gte=recently_active_since
            )
        queryset = queryset.order_by(
            "-last_activity_at",
            "-created_at",
            "-id",
        )
        return self.paginated_response(
            queryset,
            message="Chat sessions fetched successfully.",
        )


class SessionStatsAPIView(ChatSessionChatbotMixin, GenericAPIView):
    """Chatbot session/message totals and rolling 30-day comparisons."""

    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    query_serializer_class = ChatSessionQuerySerializer

    def get(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        now = timezone.now()
        current_period_start = now - timedelta(days=30)
        previous_period_start = now - timedelta(days=60)

        sessions = ChatSession.objects.filter(chatbot=chatbot)
        messages = ChatMessage.objects.filter(chat_session__chatbot=chatbot)

        session_counts = sessions.aggregate(
            total=Count("id"),
            current=Count(
                "id",
                filter=Q(
                    created_at__gte=current_period_start,
                    created_at__lt=now,
                ),
            ),
            previous=Count(
                "id",
                filter=Q(
                    created_at__gte=previous_period_start,
                    created_at__lt=current_period_start,
                ),
            ),
        )
        message_counts = messages.aggregate(
            total=Count("id"),
            current=Count(
                "id",
                filter=Q(
                    created_at__gte=current_period_start,
                    created_at__lt=now,
                ),
            ),
            previous=Count(
                "id",
                filter=Q(
                    created_at__gte=previous_period_start,
                    created_at__lt=current_period_start,
                ),
            ),
        )

        return APIResponse.success(
            data={
                "total_sessions": session_counts["total"],
                "total_messages": message_counts["total"],
                "sessions_last_30_days": session_counts["current"],
                "messages_last_30_days": message_counts["current"],
                "session_percentage_change": _percentage_change(
                    session_counts["current"],
                    session_counts["previous"],
                ),
                "message_percentage_change": _percentage_change(
                    message_counts["current"],
                    message_counts["previous"],
                ),
            },
            message="Session stats fetched successfully.",
        )


class SessionOverviewAPIView(ChatSessionChatbotMixin, GenericAPIView):
    """Daily chatbot session counts for an inclusive date range."""

    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    query_serializer_class = SessionOverviewQuerySerializer

    def get(self, request, *args, **kwargs):
        query = self.get_query()
        today = timezone.localdate()
        end_date = query.get("end_date", today)
        start_date = query.get("start_date", end_date - timedelta(days=13))

        if start_date > end_date:
            raise DjangoValidationError(
                {"end_date": "end_date must be on or after start_date."}
            )

        counts = {
            item["date"]: item["session_count"]
            for item in ChatSession.objects.filter(
                chatbot=self.get_chatbot(),
                created_at__gte=_aware_start(start_date),
                created_at__lt=_aware_start(end_date + timedelta(days=1)),
            )
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(session_count=Count("id"))
            .order_by("date")
        }

        points = []
        current_date = start_date
        while current_date <= end_date:
            points.append(
                {
                    "date": current_date.isoformat(),
                    "session_count": counts.get(current_date, 0),
                }
            )
            current_date += timedelta(days=1)

        return APIResponse.success(
            data={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "points": points,
            },
            message="Session overview fetched successfully.",
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


class ChatSessionMarkReadView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT

    def patch(self, request, *args, **kwargs):
        marked_read_count = (
            ChatMessage.objects.filter(
                chat_session=self.get_chat_session(),
                sender_type=ChatMessageSenderType.VISITOR,
            )
            .exclude(status=ChatMessageStatus.READ)
            .update(
                status=ChatMessageStatus.READ,
                updated_at=timezone.now(),
            )
        )
        return APIResponse.success(
            data={"marked_read_count": marked_read_count},
            message="Chat messages marked as read successfully.",
        )

class ChatSessionDeleteView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT

    def delete(self, request, *args, **kwargs):
        chat_session = self.get_chat_session()
        chat_session.delete()
        return APIResponse.success(
            message="Chat session deleted successfully.",
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
            .select_related("sender__user__profile")
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


class TransferSessionView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    serializer_class = TransferSessionSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        to_agent = get_object_or_404(
            ChatbotUser.objects.select_related("user__profile"),
            pk=serializer.validated_data["to_agent_id"],
            chatbot=self.get_chatbot(),
            user__is_active=True,
            is_active=True,
        )
        try:
            transfer = request_transfer(
                self.get_chat_session(),
                self.get_chatbot_user(),
                to_agent,
                reason=serializer.validated_data["reason"],
                expires_at=serializer.validated_data.get("expires_at"),
            )
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        return APIResponse.success(
            data=ChatSessionTransferSerializer(transfer).data,
            message="Ownership transfer requested successfully.",
            status=status.HTTP_201_CREATED,
        )


class IncomingTransferListView(
    ChatSessionChatbotMixin,
    PaginatedChatSessionMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    query_serializer_class = ChatSessionTransferListQuerySerializer
    serializer_class = ChatSessionTransferSerializer

    def get(self, request, *args, **kwargs):
        query = self.get_query()
        queryset = ChatSessionTransfer.objects.filter(
            chat_session__chatbot=self.get_chatbot(),
            to_agent=self.get_chatbot_user(),
        )
        expire_pending_transfers(queryset)
        queryset = queryset.select_related(
            "chat_session",
            "from_agent__user__profile",
            "to_agent__user__profile",
        )
        if query.get("status"):
            queryset = queryset.filter(status=query["status"])
        return self.paginated_response(
            queryset,
            message="Incoming ownership transfers fetched successfully.",
        )


class TransferActionView(ChatSessionTransferObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    action = None
    success_message = ""

    def post(self, request, *args, **kwargs):
        try:
            transfer = self.action(
                self.get_transfer(),
                self.get_chatbot_user(),
            )
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        return APIResponse.success(
            data=ChatSessionTransferSerializer(transfer).data,
            message=self.success_message,
        )


class AcceptTransferView(TransferActionView):
    action = staticmethod(accept_transfer)
    success_message = "Ownership transfer accepted successfully."


class DeclineTransferView(TransferActionView):
    action = staticmethod(decline_transfer)
    success_message = "Ownership transfer declined successfully."


class CancelTransferView(TransferActionView):
    action = staticmethod(cancel_transfer)
    success_message = "Ownership transfer cancelled successfully."


class ReleaseSessionView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT

    def post(self, request, *args, **kwargs):
        try:
            takeover = release_session(
                self.get_chat_session(),
                self.get_chatbot_user(),
            )
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
                self.get_chatbot_user(),
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
