from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from accounts.models import User
from analytics.api.v1.admin.serializers import AnalyticsFilterSerializer
from analytics.choices import AIUsageType
from analytics.models import AIUsage
from app.utils.permission import IsSuperAdmin
from app.utils.response import APIResponse
from chatbot.models import Chatbot
from chatbot.utils.choices import ChatbotStatusTypes
from chat.models import ChatMessage, ChatSession
from chat.utils.choices import (
    ChatMessageSenderType,
    ChatSessionChannel,
    ChatSessionStatus,
)
from workspace.models import Workspace


def _aware_start(value):
    return timezone.make_aware(
        datetime.combine(value, time.min),
        timezone.get_current_timezone(),
    )


def _shift_month(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    return date(month_index // 12, month_index % 12 + 1, 1)


def _filter_date_range(queryset, filters, *, field="created_at"):
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    if start_date:
        queryset = queryset.filter(
            **{f"{field}__gte": _aware_start(start_date)}
        )
    if end_date:
        queryset = queryset.filter(
            **{
                f"{field}__lt": _aware_start(
                    end_date + timedelta(days=1),
                )
            }
        )
    return queryset


def _period_expression(granularity, field="created_at"):
    if granularity == "day":
        return TruncDate(field)
    return TruncMonth(field)


def _period_value(value, granularity):
    if granularity == "month":
        return value.strftime("%Y-%m")
    if hasattr(value, "date") and not isinstance(value, date):
        value = value.date()
    return value.isoformat()


def _period_keys(start_date, end_date, granularity):
    if granularity == "day":
        periods = []
        current = start_date
        while current <= end_date:
            periods.append(current.isoformat())
            current += timedelta(days=1)
        return periods

    periods = []
    current = start_date.replace(day=1)
    final = end_date.replace(day=1)
    while current <= final:
        periods.append(current.strftime("%Y-%m"))
        current = _shift_month(current, 1)
    return periods


def _filters_payload(filters):
    return {
        "start_date": (
            filters["start_date"].isoformat()
            if filters.get("start_date")
            else None
        ),
        "end_date": (
            filters["end_date"].isoformat()
            if filters.get("end_date")
            else None
        ),
        "workspace": (
            filters["workspace"].slug
            if filters.get("workspace")
            else None
        ),
        "chatbot": (
            filters["chatbot"].slug
            if filters.get("chatbot")
            else None
        ),
        "granularity": filters["granularity"],
    }


def _scoped_users(filters):
    queryset = User.objects.filter(
        is_staff=False,
        is_superuser=False,
    )
    chatbot = filters.get("chatbot")
    workspace = filters.get("workspace")
    if chatbot:
        queryset = queryset.filter(
            chatbot_memberships__chatbot=chatbot,
        ).distinct()
    elif workspace:
        queryset = queryset.filter(
            Q(owned_workspaces=workspace)
            | Q(workspace_memberships__workspace=workspace)
        ).distinct()
    return _filter_date_range(queryset, filters)


def _scoped_workspaces(filters):
    queryset = Workspace.objects.all()
    chatbot = filters.get("chatbot")
    workspace = filters.get("workspace")
    if chatbot:
        queryset = queryset.filter(pk=chatbot.workspace_id)
    elif workspace:
        queryset = queryset.filter(pk=workspace.pk)
    return _filter_date_range(queryset, filters)


def _scoped_chatbots(filters):
    queryset = Chatbot.objects.all()
    chatbot = filters.get("chatbot")
    workspace = filters.get("workspace")
    if chatbot:
        queryset = queryset.filter(pk=chatbot.pk)
    elif workspace:
        queryset = queryset.filter(workspace=workspace)
    return _filter_date_range(queryset, filters)


def _scoped_sessions(filters):
    queryset = ChatSession.objects.filter(is_test=False)
    chatbot = filters.get("chatbot")
    workspace = filters.get("workspace")
    if chatbot:
        queryset = queryset.filter(chatbot=chatbot)
    elif workspace:
        queryset = queryset.filter(chatbot__workspace=workspace)
    return _filter_date_range(queryset, filters)


def _scoped_messages(filters):
    queryset = ChatMessage.objects.filter(chat_session__is_test=False)
    chatbot = filters.get("chatbot")
    workspace = filters.get("workspace")
    if chatbot:
        queryset = queryset.filter(chat_session__chatbot=chatbot)
    elif workspace:
        queryset = queryset.filter(
            chat_session__chatbot__workspace=workspace,
        )
    return _filter_date_range(queryset, filters)


def _scoped_ai_usage(filters):
    queryset = AIUsage.objects.all()
    chatbot = filters.get("chatbot")
    workspace = filters.get("workspace")
    if chatbot:
        queryset = queryset.filter(
            Q(chatbot=chatbot) | Q(chatbot_id_snapshot=chatbot.pk)
        )
    elif workspace:
        queryset = queryset.filter(chatbot__workspace=workspace)
    return _filter_date_range(queryset, filters)


class AnalyticsAPIView(GenericAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = AnalyticsFilterSerializer

    def get_filters(self):
        serializer = self.get_serializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class AIUsageAnalyticsAPIView(AnalyticsAPIView):
    """AI spend and token usage, grouped without legacy project types."""

    def get(self, request, *args, **kwargs):
        filters = self.get_filters()
        queryset = _scoped_ai_usage(filters)
        totals = queryset.aggregate(
            total_requests=Count("id"),
            total_cost=Sum("cost", default=Decimal("0")),
            average_cost=Avg("cost", default=Decimal("0")),
            total_tokens=Sum("tokens", default=0),
            input_tokens=Sum("input_tokens", default=0),
            output_tokens=Sum("output_tokens", default=0),
            thinking_tokens=Sum("thinking_tokens", default=0),
            cached_input_tokens=Sum("cached_input_tokens", default=0),
        )
        usage_counts = {
            item["usage_type"]: item
            for item in queryset.values("usage_type").annotate(
                requests=Count("id"),
                cost=Sum("cost", default=Decimal("0")),
                tokens=Sum("tokens", default=0),
            )
        }
        by_usage_type = []
        for usage_type, label in AIUsageType.choices:
            item = usage_counts.get(usage_type, {})
            by_usage_type.append(
                {
                    "usage_type": usage_type,
                    "label": label,
                    "requests": item.get("requests", 0),
                    "cost": float(item.get("cost", 0)),
                    "tokens": item.get("tokens", 0),
                }
            )

        by_model = [
            {
                "model": item["model"],
                "requests": item["requests"],
                "cost": float(item["cost"]),
                "tokens": item["tokens"],
            }
            for item in queryset.exclude(model="")
            .values("model")
            .annotate(
                requests=Count("id"),
                cost=Sum("cost", default=Decimal("0")),
                tokens=Sum("tokens", default=0),
            )
            .order_by("-cost", "model")[:10]
        ]
        granularity = filters["granularity"]
        timeline = [
            {
                "period": _period_value(item["period"], granularity),
                "requests": item["requests"],
                "cost": float(item["cost"]),
                "tokens": item["tokens"],
            }
            for item in queryset.annotate(
                period=_period_expression(granularity)
            )
            .values("period")
            .annotate(
                requests=Count("id"),
                cost=Sum("cost", default=Decimal("0")),
                tokens=Sum("tokens", default=0),
            )
            .order_by("period")
        ]

        return APIResponse.success(
            data={
                "filters": _filters_payload(filters),
                "totals": {
                    "requests": totals["total_requests"],
                    "cost": float(totals["total_cost"]),
                    "average_cost": float(totals["average_cost"]),
                    "tokens": totals["total_tokens"],
                    "input_tokens": totals["input_tokens"],
                    "output_tokens": totals["output_tokens"],
                    "thinking_tokens": totals["thinking_tokens"],
                    "cached_input_tokens": totals["cached_input_tokens"],
                },
                "by_usage_type": by_usage_type,
                "by_model": by_model,
                "timeline": timeline,
            },
            message="AI usage analytics fetched successfully.",
        )


class PlatformAnalyticsAPIView(AnalyticsAPIView):
    """User, workspace, and chatbot totals for the selected scope."""

    def get(self, request, *args, **kwargs):
        filters = self.get_filters()
        users = _scoped_users(filters).aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(is_active=True)),
            inactive=Count("id", filter=Q(is_active=False)),
            email_verified=Count(
                "id",
                filter=Q(is_email_verified=True),
            ),
        )
        workspaces = _scoped_workspaces(filters).aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(is_active=True)),
            inactive=Count("id", filter=Q(is_active=False)),
        )
        chatbots = _scoped_chatbots(filters).aggregate(
            total=Count("id"),
            active=Count(
                "id",
                filter=Q(
                    is_deleted=False,
                    status=ChatbotStatusTypes.ACTIVE,
                ),
            ),
            draft=Count(
                "id",
                filter=Q(
                    is_deleted=False,
                    status=ChatbotStatusTypes.DRAFT,
                ),
            ),
            disabled=Count(
                "id",
                filter=Q(
                    is_deleted=False,
                    status__in=(
                        ChatbotStatusTypes.DISABLED,
                        ChatbotStatusTypes.DISABLED_BY_ADMIN,
                    ),
                ),
            ),
            deleted=Count("id", filter=Q(is_deleted=True)),
        )
        return APIResponse.success(
            data={
                "filters": _filters_payload(filters),
                "users": users,
                "workspaces": workspaces,
                "chatbots": chatbots,
            },
            message="Platform analytics fetched successfully.",
        )


class GrowthAnalyticsAPIView(AnalyticsAPIView):
    """Aligned user, workspace, and chatbot creation trends."""

    @staticmethod
    def _counts(queryset, granularity):
        return {
            _period_value(item["period"], granularity): item["count"]
            for item in queryset.annotate(
                period=_period_expression(granularity)
            )
            .values("period")
            .annotate(count=Count("id"))
            .order_by("period")
        }

    def get(self, request, *args, **kwargs):
        filters = dict(self.get_filters())
        if not filters.get("end_date"):
            filters["end_date"] = timezone.localdate()
        if not filters.get("start_date"):
            end_month = filters["end_date"].replace(day=1)
            filters["start_date"] = _shift_month(end_month, -11)

        granularity = filters["granularity"]
        user_counts = self._counts(_scoped_users(filters), granularity)
        workspace_counts = self._counts(
            _scoped_workspaces(filters),
            granularity,
        )
        chatbot_counts = self._counts(
            _scoped_chatbots(filters),
            granularity,
        )
        periods = _period_keys(
            filters["start_date"],
            filters["end_date"],
            granularity,
        )
        points = [
            {
                "period": period,
                "users": user_counts.get(period, 0),
                "workspaces": workspace_counts.get(period, 0),
                "chatbots": chatbot_counts.get(period, 0),
            }
            for period in periods
        ]
        return APIResponse.success(
            data={
                "filters": _filters_payload(filters),
                "totals": {
                    "users": sum(user_counts.values()),
                    "workspaces": sum(workspace_counts.values()),
                    "chatbots": sum(chatbot_counts.values()),
                },
                "points": points,
            },
            message="Growth analytics fetched successfully.",
        )


class ConversationAnalyticsAPIView(AnalyticsAPIView):
    """Live session and message volume, excluding test conversations."""

    def get(self, request, *args, **kwargs):
        filters = self.get_filters()
        sessions = _scoped_sessions(filters)
        messages = _scoped_messages(filters)
        session_total = sessions.count()
        message_total = messages.count()
        status_counts = {
            item["status"]: item["count"]
            for item in sessions.values("status").annotate(count=Count("id"))
        }
        channel_counts = {
            item["channel"]: item["count"]
            for item in sessions.values("channel").annotate(count=Count("id"))
        }
        sender_counts = {
            item["sender_type"]: item["count"]
            for item in messages.values("sender_type").annotate(
                count=Count("id")
            )
        }
        granularity = filters["granularity"]
        session_timeline = {
            _period_value(item["period"], granularity): item["count"]
            for item in sessions.annotate(
                period=_period_expression(granularity)
            )
            .values("period")
            .annotate(count=Count("id"))
            .order_by("period")
        }
        message_timeline = {
            _period_value(item["period"], granularity): item["count"]
            for item in messages.annotate(
                period=_period_expression(granularity)
            )
            .values("period")
            .annotate(count=Count("id"))
            .order_by("period")
        }
        periods = sorted(set(session_timeline) | set(message_timeline))

        return APIResponse.success(
            data={
                "filters": _filters_payload(filters),
                "totals": {
                    "sessions": session_total,
                    "messages": message_total,
                },
                "sessions_by_status": [
                    {
                        "status": value,
                        "label": label,
                        "count": status_counts.get(value, 0),
                    }
                    for value, label in ChatSessionStatus.choices
                ],
                "sessions_by_channel": [
                    {
                        "channel": value,
                        "label": label,
                        "count": channel_counts.get(value, 0),
                    }
                    for value, label in ChatSessionChannel.choices
                ],
                "messages_by_sender": [
                    {
                        "sender_type": value,
                        "label": label,
                        "count": sender_counts.get(value, 0),
                    }
                    for value, label in ChatMessageSenderType.choices
                ],
                "timeline": [
                    {
                        "period": period,
                        "sessions": session_timeline.get(period, 0),
                        "messages": message_timeline.get(period, 0),
                    }
                    for period in periods
                ],
            },
            message="Conversation analytics fetched successfully.",
        )
