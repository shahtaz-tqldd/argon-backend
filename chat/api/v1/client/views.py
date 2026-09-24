from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Exists, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import GenericAPIView

from app.utils.logger import logger
from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotPermissionTypes
from chat.api.v1.client.serializers import (
    AgentMessageCreateSerializer,
    ChatSessionTranscriptQuerySerializer,
    ChatbotAgentSerializer,
    ChatMessageSerializer,
    ChatSessionListSerializer,
    ChatSessionListQuerySerializer,
    ChatSessionObjectQuerySerializer,
    ChatSessionQuerySerializer,
    ChatSessionSerializer,
    TestChatCapacitySerializer,
    TestChatMessageCreateSerializer,
    TestChatSessionSerializer,
    ChatSessionTakeoverSerializer,
    ChatSessionTransferListQuerySerializer,
    ChatSessionTransferObjectQuerySerializer,
    ChatSessionTransferSerializer,
    ForceReturnToAISerializer,
    ResolveSessionSerializer,
    SessionOverviewQuerySerializer,
    TakeOverSessionSerializer,
    TransferSessionSerializer,
)
from chat.models import (
    ChatbotBlockedVisitor,
    ChatMessage,
    ChatSession,
    ChatSessionTransfer,
)
from chat.services.messages import send_agent_message
from chat.services.transcripts import build_transcript
from chat.services.takeover import (
    accept_transfer,
    cancel_transfer,
    decline_transfer,
    expire_pending_transfers,
    force_return_to_ai,
    release_session,
    request_transfer,
    resolve_session,
    take_over_session,
)
from chat.services.test_sessions import (
    TestChatMessageLimitExceeded,
    create_test_session,
    send_test_message,
)
from chat.utils.choices import (
    ChatMessageSenderType,
    ChatMessageStatus,
    ChatSessionTransferStatus,
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
                is_test=False,
            )
            self.check_object_permissions(self.request, self._chat_session)
        return self._chat_session


class TestChatSessionObjectMixin(ChatSessionChatbotMixin):
    query_serializer_class = ChatSessionObjectQuerySerializer
    _test_chat_session = None

    def get_test_chat_session(self):
        if self._test_chat_session is None:
            self._test_chat_session = get_object_or_404(
                ChatSession.objects.select_related(
                    "chatbot",
                    "chatbot__workspace",
                ),
                pk=self.get_query()["session_id"],
                chatbot=self.get_chatbot(),
                is_test=True,
            )
            self.check_object_permissions(
                self.request,
                self._test_chat_session,
            )
        return self._test_chat_session


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
                chat_session__is_test=False,
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
        unread_messages = (
            ChatMessage.objects.filter(
                chat_session=OuterRef("pk"),
                sender_type=ChatMessageSenderType.VISITOR,
            )
            .exclude(status=ChatMessageStatus.READ)
            .order_by()
            .values("chat_session")
            .annotate(total=Count("id"))
            .values("total")
        )
        pending_transfer = ChatSessionTransfer.objects.filter(
            chat_session=OuterRef("pk"),
            status=ChatSessionTransferStatus.PENDING,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gte=timezone.now())
        )
        blocked_visitor = ChatbotBlockedVisitor.objects.filter(
            chatbot_id=OuterRef("chatbot_id"),
            visitor_id=OuterRef("visitor_id"),
        )
        queryset = (
            ChatSession.objects.filter(
                chatbot=self.get_chatbot(),
                is_test=False,
            )
            .select_related(
                "chatbot",
                "lead",
                "assigned_to__user",
            )
            .annotate(
                unread_message_count=Coalesce(
                    Subquery(unread_messages),
                    Value(0),
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
                transfer_requested_to_name=Subquery(
                    pending_transfer.values("to_agent__user__name")[:1]
                ),
                is_blocked=Exists(blocked_visitor),
            )
        )
        if query.get("status"):
            queryset = queryset.filter(status=query["status"])
        channel = query.get("channel")
        if channel == "web_widget":
            queryset = queryset.filter(channel=channel)
        elif channel:
            queryset = queryset.none()
        assignment = query.get("assignment", "all")
        if assignment == "mine":
            queryset = queryset.filter(assigned_to=self.get_chatbot_user())
        elif assignment == "assigned":
            queryset = queryset.filter(assigned_to__isnull=False)
        elif assignment == "unassigned":
            queryset = queryset.filter(assigned_to__isnull=True)
        assigned_to = query.get("assigned_to")
        if assigned_to:
            queryset = queryset.filter(
                Q(
                    assigned_to__user__email__iexact=assigned_to,
                    assigned_to__is_active=True,
                    assigned_to__user__is_active=True,
                )
                | (
                    Q(
                        transfers__to_agent__user__email__iexact=assigned_to,
                        transfers__to_agent__is_active=True,
                        transfers__to_agent__user__is_active=True,
                        transfers__status=ChatSessionTransferStatus.PENDING,
                    )
                    & (
                        Q(transfers__expires_at__isnull=True)
                        | Q(transfers__expires_at__gte=timezone.now())
                    )
                )
            ).distinct()
        if query.get("my_session"):
            chatbot_user = self.get_chatbot_user()
            queryset = queryset.filter(
                Q(assigned_to=chatbot_user)
                | (
                    Q(
                        transfers__to_agent=chatbot_user,
                        transfers__status=ChatSessionTransferStatus.PENDING,
                    )
                    & (
                        Q(transfers__expires_at__isnull=True)
                        | Q(transfers__expires_at__gte=timezone.now())
                    )
                )
            ).distinct()
        search = query.get("search")
        if search:
            queryset = queryset.filter(
                Q(messages__content__icontains=search)
                | Q(lead__collected_fields__name__icontains=search)
                | Q(lead__collected_fields__email__icontains=search)
                | Q(user_metadata__name__icontains=search)
                | Q(user_metadata__email__icontains=search)
            ).distinct()
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


class TestChatSessionCreateView(ChatSessionChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    chatbot_admin_only = True
    query_serializer_class = ChatSessionQuerySerializer
    serializer_class = TestChatSessionSerializer

    def post(self, request, *args, **kwargs):
        session = create_test_session(self.get_chatbot())
        return APIResponse.success(
            data=self.get_serializer(session).data,
            message="Test chat session created successfully.",
            status=status.HTTP_201_CREATED,
        )


class TestChatSessionListView(
    ChatSessionChatbotMixin,
    PaginatedChatSessionMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    chatbot_admin_only = True
    query_serializer_class = ChatSessionQuerySerializer
    serializer_class = TestChatSessionSerializer

    def get(self, request, *args, **kwargs):
        queryset = (
            ChatSession.objects.filter(
                chatbot=self.get_chatbot(),
                is_test=True,
            )
            .annotate(message_count=Count("messages"))
            .order_by("-last_activity_at", "-created_at", "-id")
        )
        return self.paginated_response(
            queryset,
            message="Test chat sessions fetched successfully.",
        )


class TestChatSessionDetailView(TestChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    chatbot_admin_only = True
    serializer_class = TestChatSessionSerializer

    def get(self, request, *args, **kwargs):
        session = self.get_test_chat_session()
        session.message_count = session.messages.count()
        return APIResponse.success(
            data=self.get_serializer(session).data,
            message="Test chat session fetched successfully.",
        )


class TestChatMessageListView(
    TestChatSessionObjectMixin,
    PaginatedChatSessionMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    chatbot_admin_only = True
    serializer_class = ChatMessageSerializer

    def get(self, request, *args, **kwargs):
        messages = ChatMessage.objects.filter(
            chat_session=self.get_test_chat_session(),
        ).select_related("sender__user__profile")
        return self.paginated_response(
            messages,
            message="Test chat messages fetched successfully.",
        )


class TestChatMessageCreateView(TestChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    chatbot_admin_only = True
    serializer_class = TestChatMessageCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reply = send_test_message(
                self.get_test_chat_session(),
                content=serializer.validated_data["content"],
            )
        except TestChatMessageLimitExceeded as exc:
            return APIResponse.error(
                errors={"capacity": [str(exc)]},
                message=str(exc),
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            logger.exception("Test chat AI response generation failed.")
            return APIResponse.error(
                message="The test chat response could not be generated.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return APIResponse.success(
            data={
                "visitor_message": ChatMessageSerializer(
                    reply.visitor_message,
                ).data,
                "ai_message": ChatMessageSerializer(reply.ai_message).data,
                "usage_source": reply.usage_source,
                "capacity": TestChatCapacitySerializer(reply.capacity).data,
            },
            message="Test chat response generated successfully.",
            status=status.HTTP_201_CREATED,
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

        sessions = ChatSession.objects.filter(chatbot=chatbot, is_test=False)
        messages = ChatMessage.objects.filter(
            chat_session__chatbot=chatbot,
            chat_session__is_test=False,
        )

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
                is_test=False,
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


class BlockVisitorView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    chatbot_admin_only = True

    def post(self, request, *args, **kwargs):
        chat_session = self.get_chat_session()
        if not chat_session.visitor_id:
            return APIResponse.error(
                errors={"visitor_id": ["This session has no visitor ID."]},
                message="Visitor could not be blocked.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        blocking_agent = ChatbotUser.objects.filter(
            chatbot=chat_session.chatbot,
            user=request.user,
            is_active=True,
        ).first()
        blocked_visitor, created = ChatbotBlockedVisitor.objects.get_or_create(
            chatbot=chat_session.chatbot,
            visitor_id=chat_session.visitor_id,
            defaults={"blocked_by": blocking_agent},
        )
        return APIResponse.success(
            data={
                "visitor_id": blocked_visitor.visitor_id,
                "blocked": True,
                "already_blocked": not created,
            },
            message=(
                "Visitor blocked successfully."
                if created
                else "Visitor is already blocked."
            ),
            status=(
                status.HTTP_201_CREATED
                if created
                else status.HTTP_200_OK
            ),
        )


class ChatSessionTranscriptView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    query_serializer_class = ChatSessionTranscriptQuerySerializer

    def perform_content_negotiation(self, request, force=False):
        # DRF reserves `format` for renderer overrides. This endpoint uses it
        # as the requested download format and returns its own HttpResponse.
        renderers = self.get_renderers()
        renderer = renderers[0]
        return renderer, renderer.media_type

    def get(self, request, *args, **kwargs):
        chat_session = self.get_chat_session()
        file_format = self.get_query()["file_format"]
        content = build_transcript(chat_session, file_format)
        content_types = {
            "csv": "text/csv; charset=utf-8",
            "pdf": "application/pdf",
        }
        response = HttpResponse(
            content,
            content_type=content_types[file_format],
        )
        response["Content-Disposition"] = (
            f'attachment; filename="chat-session-{chat_session.id}-transcript.'
            f'{file_format}"'
        )
        response["X-Transcript-Message-Limit"] = "350"
        return response


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
    serializer_class = TakeOverSessionSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            takeover = take_over_session(
                self.get_chat_session(),
                self.get_chatbot_user(),
                is_forced=serializer.validated_data["is_forced"],
                takeover_reason=serializer.validated_data["reason"],
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


class SessionTransferStatusView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT

    def get(self, request, *args, **kwargs):
        chat_session = self.get_chat_session()
        queryset = ChatSessionTransfer.objects.filter(chat_session=chat_session)
        expire_pending_transfers(queryset)
        pending_transfer = (
            queryset.filter(status=ChatSessionTransferStatus.PENDING)
            .select_related(
                "from_agent__user__profile",
                "to_agent__user__profile",
            )
            .first()
        )
        return APIResponse.success(
            data={
                "chat_session_id": str(chat_session.id),
                "assigned_to": (
                    ChatbotAgentSerializer(chat_session.assigned_to).data
                    if chat_session.assigned_to_id
                    else None
                ),
                "has_pending_transfer": pending_transfer is not None,
                "transfer": (
                    ChatSessionTransferSerializer(pending_transfer).data
                    if pending_transfer is not None
                    else None
                ),
            },
            message="Session transfer status fetched successfully.",
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
            chat_session__is_test=False,
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


class ForceReturnToAIView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
    serializer_class = ForceReturnToAISerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            takeover = force_return_to_ai(
                self.get_chat_session(),
                self.get_chatbot_user(),
                **serializer.validated_data,
            )
        except DjangoValidationError as exc:
            return validation_error_response(exc)
        return APIResponse.success(
            data=ChatSessionTakeoverSerializer(takeover).data,
            message="Chat session forcefully returned to AI.",
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
