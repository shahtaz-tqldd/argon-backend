from datetime import date, datetime, time, timedelta

from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView

from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from chatbot.models import Chatbot, ChatbotConfig, ChatbotUser
from chatbot.services.capacity import get_chatbot_capacity
from chatbot.services import record_chatbot_activity
from chatbot.services.activity_logs import activity_serializer_snapshot, activity_update_metadata
from chatbot.utils.choices import ChatbotPermissionTypes
from lead_capture.api.v1.client.serializers import (
    LeadAIInsightSerializer,
    LeadCaptureConfigSerializer,
    LeadChatbotQuerySerializer,
    LeadDetailSerializer,
    LeadExportQuerySerializer,
    LeadGrowthQuerySerializer,
    LeadNoteQuerySerializer,
    LeadNoteSerializer,
    LeadQuerySerializer,
    LeadSerializer,
    LeadSignalDetailSerializer,
    LeadSignalQuerySerializer,
    LeadSignalSerializer,
    LeadUpdateSerializer,
)
from lead_capture.models import (
    Lead,
    LeadAIInsight,
    LeadCaptureConfig,
    LeadNote,
    LeadSignal,
)
from lead_capture.services.exports import build_lead_export
from subscription.choices import PlanFeature


class PaginatedLeadMixin:
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


class LeadCaptureChatbotMixin:
    _chatbot = None
    _chatbot_query = None
    chatbot_query_serializer_class = LeadChatbotQuerySerializer

    def get_chatbot_query(self):
        if self._chatbot_query is None:
            serializer = self.chatbot_query_serializer_class(
                data=self.request.query_params,
            )
            serializer.is_valid(raise_exception=True)
            self._chatbot_query = serializer.validated_data
        return self._chatbot_query

    def get_chatbot(self):
        if self._chatbot is None:
            self._chatbot = get_object_or_404(
                Chatbot.objects.select_related("workspace"),
                slug=self.get_chatbot_query()["chatbot_slug"],
                is_deleted=False,
                workspace__is_active=True,
            )
            self.check_object_permissions(self.request, self._chatbot)
            try:
                capacity = get_chatbot_capacity(self._chatbot)
            except ChatbotConfig.DoesNotExist as exc:
                raise PermissionDenied(
                    "Chatbot capacity has not been initialized."
                ) from exc
            if not capacity.has_feature(PlanFeature.LEAD_CAPTURE):
                raise PermissionDenied(
                    "The active subscription does not include lead capture."
                )
        return self._chatbot

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["chatbot"] = self.get_chatbot()
        return context


class LeadObjectMixin(LeadCaptureChatbotMixin):
    _lead = None
    chatbot_query_serializer_class = LeadQuerySerializer

    def get_lead(self):
        if self._lead is None:
            self._lead = get_object_or_404(
                Lead.objects.select_related(
                    "chatbot",
                    "chatbot__workspace",
                    "visitor",
                ),
                pk=self.get_chatbot_query()["lead_id"],
                chatbot=self.get_chatbot(),
            )
            self.check_object_permissions(self.request, self._lead)
        return self._lead


class LeadNoteObjectMixin(LeadObjectMixin):
    _lead_note = None
    chatbot_query_serializer_class = LeadNoteQuerySerializer

    def get_lead_note(self):
        if self._lead_note is None:
            self._lead_note = get_object_or_404(
                LeadNote.objects.select_related(
                    "author__user",
                    "lead",
                    "lead__chatbot",
                    "lead__chatbot__workspace",
                ),
                pk=self.get_chatbot_query()["note_id"],
                lead=self.get_lead(),
            )
        return self._lead_note


class LeadDateRangeQueryMixin:
    """Inclusive created_at filtering driven by LeadSignalQuerySerializer."""

    chatbot_query_serializer_class = LeadSignalQuerySerializer

    @staticmethod
    def _start_of_day(value):
        return timezone.make_aware(
            datetime.combine(value, time.min),
            timezone.get_current_timezone(),
        )

    def filter_by_date_range(self, queryset):
        query = self.get_chatbot_query()
        start_date = query.get("start_date")
        if start_date:
            queryset = queryset.filter(
                created_at__gte=self._start_of_day(start_date),
            )
        end_date = query.get("end_date")
        if end_date:
            queryset = queryset.filter(
                created_at__lt=self._start_of_day(end_date + timedelta(days=1)),
            )
        return queryset


class LeadCaptureConfigAPIView(LeadCaptureChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    serializer_class = LeadCaptureConfigSerializer

    def get(self, request, *args, **kwargs):
        config = get_object_or_404(
            LeadCaptureConfig,
            chatbot=self.get_chatbot(),
        )
        return APIResponse.success(
            data=self.get_serializer(config).data,
            message="Lead capture configuration fetched successfully.",
        )


class LeadCaptureConfigCreateAPIView(LeadCaptureChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    serializer_class = LeadCaptureConfigSerializer

    def post(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        if LeadCaptureConfig.objects.filter(chatbot=chatbot).exists():
            return APIResponse.error(
                errors={
                    "chatbot_slug": [
                        "A lead capture configuration already exists for this chatbot."
                    ]
                },
                message="Lead capture configuration already exists.",
                status=status.HTTP_409_CONFLICT,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        config = serializer.save()
        record_chatbot_activity(
            chatbot=chatbot,
            user=request.user,
            module="leads",
            action="leads.config.created",
            description="Created lead capture configuration.",
            metadata={
                "config_id": str(config.id),
                "configuration": activity_serializer_snapshot(serializer, config),
            },
        )
        return APIResponse.success(
            data=self.get_serializer(config).data,
            message="Lead capture configuration created successfully.",
            status=status.HTTP_201_CREATED,
        )


class LeadCaptureConfigUpdateAPIView(LeadCaptureChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    serializer_class = LeadCaptureConfigSerializer

    def _update(self, request, *, partial):
        config = get_object_or_404(
            LeadCaptureConfig,
            chatbot=self.get_chatbot(),
        )
        serializer = self.get_serializer(
            config,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        fields = tuple(serializer.validated_data)
        previous = activity_serializer_snapshot(serializer, config, fields)
        config = serializer.save()
        record_chatbot_activity(
            chatbot=config.chatbot,
            user=request.user,
            module="leads",
            action="leads.config.updated",
            description="Updated lead capture configuration.",
            metadata={
                "config_id": str(config.id),
                **activity_update_metadata(
                    previous, activity_serializer_snapshot(serializer, config, fields),
                ),
            },
        )
        return APIResponse.success(
            data=self.get_serializer(config).data,
            message="Lead capture configuration updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class LeadListView(
    LeadCaptureChatbotMixin,
    PaginatedLeadMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadSerializer

    def get(self, request, *args, **kwargs):
        queryset = (
            Lead.objects.filter(chatbot=self.get_chatbot())
            .annotate(notes_count=Count("notes"))
            .order_by("-created_at")
        )
        return self.paginated_response(
            queryset,
            message="Leads fetched successfully.",
        )


class LeadGrowthAPIView(
    LeadCaptureChatbotMixin,
    LeadDateRangeQueryMixin,
    GenericAPIView,
):
    """Day/week/month lead-capture growth series for a chart.

    Defaults to the last 15 days (inclusive) at day granularity; supports
    an inclusive start_date/end_date range and day|week|month intervals.
    Buckets are zero-filled so charts get a continuous axis, with a
    running cumulative total per bucket.
    """

    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    chatbot_query_serializer_class = LeadGrowthQuerySerializer
    default_window_days = 15
    trunc_classes = {
        "day": TruncDay,
        "week": TruncWeek,
        "month": TruncMonth,
    }

    def get(self, request, *args, **kwargs):
        query = self.get_chatbot_query()
        end_date = query.get("end_date") or timezone.localdate()
        start_date = query.get("start_date") or end_date - timedelta(
            days=self.default_window_days - 1,
        )
        interval = query["interval"]

        leads = Lead.objects.filter(
            chatbot=self.get_chatbot(),
            created_at__gte=self._start_of_day(start_date),
            created_at__lt=self._start_of_day(end_date + timedelta(days=1)),
        )
        counts = {
            row["bucket"].date(): row["count"]
            for row in leads.annotate(
                bucket=self.trunc_classes[interval](
                    "created_at",
                    tzinfo=timezone.get_current_timezone(),
                ),
            ).values("bucket").annotate(count=Count("id"))
        }

        growth = []
        cumulative_total = 0
        for bucket_date in self._iter_buckets(start_date, end_date, interval):
            count = counts.get(bucket_date, 0)
            cumulative_total += count
            growth.append(
                {
                    "date": bucket_date.isoformat(),
                    "count": count,
                    "cumulative_total": cumulative_total,
                }
            )

        return APIResponse.success(
            data={
                "interval": interval,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "total_leads": cumulative_total,
                "growth": growth,
            },
            message="Lead growth fetched successfully.",
        )

    @staticmethod
    def _iter_buckets(start_date, end_date, interval):
        if interval == "week":
            current = start_date - timedelta(days=start_date.weekday())
            while current <= end_date:
                yield current
                current += timedelta(days=7)
        elif interval == "month":
            year, month = start_date.year, start_date.month
            while (year, month) <= (end_date.year, end_date.month):
                yield date(year, month, 1)
                year, month = (
                    (year + 1, 1) if month == 12 else (year, month + 1)
                )
        else:
            current = start_date
            while current <= end_date:
                yield current
                current += timedelta(days=1)


class ExportLeadAPIView(LeadCaptureChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    chatbot_query_serializer_class = LeadExportQuerySerializer

    export_limit = 1000

    @staticmethod
    def _start_of_day(value):
        return timezone.make_aware(
            datetime.combine(value, time.min),
            timezone.get_current_timezone(),
        )

    def get(self, request, *args, **kwargs):
        query = self.get_chatbot_query()
        chatbot = self.get_chatbot()
        leads = Lead.objects.filter(chatbot=chatbot)

        start_date = query.get("start_date")
        if start_date:
            leads = leads.filter(
                created_at__gte=self._start_of_day(start_date),
            )

        end_date = query.get("end_date")
        if end_date:
            leads = leads.filter(
                created_at__lt=self._start_of_day(
                    end_date + timedelta(days=1),
                ),
            )

        exported_leads = list(
            leads.order_by("-created_at", "-id")[: self.export_limit]
        )
        file_format = query["file_format"]
        content = build_lead_export(exported_leads, file_format)
        content_types = {
            "csv": "text/csv; charset=utf-8",
            "xlsx": (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        }
        filename = (
            f"{chatbot.slug}-leads-{timezone.localdate().isoformat()}."
            f"{file_format}"
        )
        response = HttpResponse(
            content,
            content_type=content_types[file_format],
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{filename}"'
        )
        response["X-Lead-Export-Limit"] = str(self.export_limit)
        response["X-Lead-Export-Count"] = str(len(exported_leads))
        record_chatbot_activity(
            chatbot=chatbot,
            user=request.user,
            module="leads",
            action="leads.exported",
            description="Exported leads.",
            metadata={
                "file_format": file_format,
                "filename": filename,
                "count": len(exported_leads),
                "lead_ids": [str(lead.id) for lead in exported_leads],
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
            },
        )
        return response


class LeadDetailView(LeadObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadDetailSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_lead()).data,
            message="Lead fetched successfully.",
        )


class LeadUpdateView(LeadObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadUpdateSerializer

    def _update(self, request, *, partial):
        lead = self.get_lead()
        serializer = self.get_serializer(
            lead,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        fields = tuple(serializer.validated_data)
        previous = activity_serializer_snapshot(serializer, lead, fields)
        lead = serializer.save()
        record_chatbot_activity(
            chatbot=lead.chatbot,
            user=request.user,
            module="leads",
            action="leads.updated",
            description="Updated lead.",
            metadata={
                "lead_id": str(lead.id),
                **activity_update_metadata(
                    previous, activity_serializer_snapshot(serializer, lead, fields),
                ),
            },
        )
        return APIResponse.success(
            data=LeadSerializer(lead).data,
            message="Lead updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class LeadSignalHistoryAPIView(LeadObjectMixin, PaginatedLeadMixin, GenericAPIView):
    """Paginated qualification-signal history for a single lead."""

    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadSignalSerializer

    def get(self, request, *args, **kwargs):
        signals = (
            LeadSignal.objects.filter(lead=self.get_lead())
            .select_related("message")
            .order_by("-created_at", "-id")
        )
        return self.paginated_response(
            signals,
            message="Lead signal history fetched successfully.",
        )


class LeadSignalListAPIView(
    LeadCaptureChatbotMixin,
    LeadDateRangeQueryMixin,
    PaginatedLeadMixin,
    GenericAPIView,
):
    """All qualification signals for a chatbot, newest first.

    Each page item is one signal carrying its score and the chat message
    the score was recorded with, when any.
    """

    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadSignalDetailSerializer

    def get(self, request, *args, **kwargs):
        signals = self.filter_by_date_range(
            LeadSignal.objects.filter(lead__chatbot=self.get_chatbot())
            .select_related("message")
            .order_by("-created_at", "-id")
        )
        return self.paginated_response(
            signals,
            message="Lead signals fetched successfully.",
        )


class LeadSignalStatsAPIView(
    LeadCaptureChatbotMixin,
    LeadDateRangeQueryMixin,
    GenericAPIView,
):
    """Score-wise lead separation for a chatbot.

    Buckets use the denormalized lead average: hot (> 80), medium
    (> 50 and <= 80), and low (<= 50). Leads without a score count toward
    the total but fall into no bucket. Defaults to all time; supports an
    inclusive start_date/end_date range on lead creation.
    """

    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    hot_lead_score_threshold = 80
    medium_lead_score_threshold = 50

    def get(self, request, *args, **kwargs):
        leads = self.filter_by_date_range(
            Lead.objects.filter(chatbot=self.get_chatbot())
        )
        summary = leads.aggregate(
            total_leads=Count("id"),
            hot_leads=Count(
                "id",
                filter=Q(avg_score__gt=self.hot_lead_score_threshold),
            ),
            medium_leads=Count(
                "id",
                filter=Q(
                    avg_score__gt=self.medium_lead_score_threshold,
                    avg_score__lte=self.hot_lead_score_threshold,
                ),
            ),
            low_leads=Count(
                "id",
                filter=Q(avg_score__lte=self.medium_lead_score_threshold),
            ),
            average_lead_score=Avg("avg_score"),
        )
        average_score = summary["average_lead_score"]
        return APIResponse.success(
            data={
                "total_leads": summary["total_leads"],
                "hot_leads": summary["hot_leads"],
                "medium_leads": summary["medium_leads"],
                "low_leads": summary["low_leads"],
                "unscored_leads": (
                    summary["total_leads"]
                    - summary["hot_leads"]
                    - summary["medium_leads"]
                    - summary["low_leads"]
                ),
                "average_lead_score": (
                    round(float(average_score), 2)
                    if average_score is not None
                    else None
                ),
            },
            message="Lead signal stats fetched successfully.",
        )


class LeadAIInsightListView(
    LeadCaptureChatbotMixin,
    PaginatedLeadMixin,
    GenericAPIView,
):
    """Paginated weekly AI insights for a chatbot, newest week first."""

    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadAIInsightSerializer

    def get(self, request, *args, **kwargs):
        insights = (
            LeadAIInsight.objects.filter(chatbot=self.get_chatbot())
            .order_by("-week_start", "-created_at")
        )
        return self.paginated_response(
            insights,
            message="Lead AI insights fetched successfully.",
        )


class LeadNoteListView(LeadObjectMixin, PaginatedLeadMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadNoteSerializer

    def get(self, request, *args, **kwargs):
        notes = LeadNote.objects.filter(lead=self.get_lead()).select_related(
            "author__user"
        )
        return self.paginated_response(
            notes,
            message="Lead notes fetched successfully.",
        )


class LeadNoteCreateView(LeadObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadNoteSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["lead"] = self.get_lead()
        return context

    def post(self, request, *args, **kwargs):
        chatbot_user = get_object_or_404(
            ChatbotUser.objects.select_related("user"),
            chatbot=self.get_chatbot(),
            user=request.user,
            is_active=True,
        )
        context = self.get_serializer_context()
        context["chatbot_user"] = chatbot_user
        serializer = self.get_serializer(data=request.data, context=context)
        serializer.is_valid(raise_exception=True)
        note = serializer.save()
        return APIResponse.success(
            data=LeadNoteSerializer(note).data,
            message="Lead note created successfully.",
            status=status.HTTP_201_CREATED,
        )


class LeadNoteDetailView(LeadNoteObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadNoteSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_lead_note()).data,
            message="Lead note fetched successfully.",
        )


class LeadNoteUpdateView(LeadNoteObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadNoteSerializer

    def _update(self, request, *, partial):
        note = self.get_lead_note()
        serializer = self.get_serializer(
            note,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        fields = tuple(serializer.validated_data)
        previous = activity_serializer_snapshot(serializer, note, fields)
        note = serializer.save()
        record_chatbot_activity(
            chatbot=note.lead.chatbot,
            user=request.user,
            module="leads",
            action="leads.note.updated",
            description="Updated lead note.",
            metadata={
                "lead_id": str(note.lead_id), "note_id": str(note.id),
                **activity_update_metadata(
                    previous, activity_serializer_snapshot(serializer, note, fields),
                ),
            },
        )
        return APIResponse.success(
            data=self.get_serializer(note).data,
            message="Lead note updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class LeadNoteDeleteView(LeadNoteObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadNoteSerializer

    def delete(self, request, *args, **kwargs):
        note = self.get_lead_note()
        note_id = str(note.id)
        chatbot = note.lead.chatbot
        metadata = {"lead_id": str(note.lead_id), "note_id": note_id, "content": note.content}
        note.delete()
        record_chatbot_activity(
            chatbot=chatbot,
            user=request.user,
            module="leads",
            action="leads.note.deleted",
            description="Deleted lead note.",
            metadata=metadata,
        )
        return APIResponse.success(
            data={"id": note_id},
            message="Lead note deleted successfully.",
        )
