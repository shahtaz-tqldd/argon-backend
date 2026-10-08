from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView

from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from appointment.api.v1.client.serializers import (
    AppointmentBookingAvailabilitySerializer,
    AppointmentBookingConfigSerializer,
    AppointmentChatbotQuerySerializer,
    AppointmentQuerySerializer,
    AppointmentSerializer,
    AppointmentStatsQuerySerializer,
    AppointmentUpdateSerializer,
)
from appointment.models import Appointment, AppointmentBookingConfig
from appointment.utils.choices import AppointmentStatus
from chatbot.models import Chatbot, ChatbotConfig
from chatbot.services.chatbot_config import get_chatbot_capacity
from chatbot.services import record_chatbot_activity
from chatbot.services.activity_logs import activity_serializer_snapshot, activity_update_metadata
from chatbot.utils.choices import ChatbotPermissionTypes
from subscription.utils.choices import PlanFeature


class PaginatedAppointmentMixin:
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


class AppointmentBookingChatbotMixin:
    _chatbot = None
    _chatbot_query = None
    chatbot_query_serializer_class = AppointmentChatbotQuerySerializer

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
            if not capacity.has_feature(PlanFeature.APPOINTMENT_BOOKING):
                raise PermissionDenied(
                    "The active subscription does not include appointment booking."
                )
        return self._chatbot

    def get_config(self):
        config, _created = AppointmentBookingConfig.objects.get_or_create(
            chatbot=self.get_chatbot(),
            defaults={
                "created_by": self.request.user,
                "updated_by": self.request.user,
            },
        )
        return config

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["chatbot"] = self.get_chatbot()
        return context


class AppointmentObjectMixin(AppointmentBookingChatbotMixin):
    _appointment = None
    chatbot_query_serializer_class = AppointmentQuerySerializer

    def get_appointment(self):
        if self._appointment is None:
            self._appointment = get_object_or_404(
                Appointment.objects.select_related(
                    "chatbot",
                    "chatbot__workspace",
                ),
                pk=self.get_chatbot_query()["appointment_id"],
                chatbot=self.get_chatbot(),
            )
            self.check_object_permissions(self.request, self._appointment)
        return self._appointment


class AppointmentBookingConfigAPIView(
    AppointmentBookingChatbotMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    serializer_class = AppointmentBookingConfigSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_config()).data,
            message="Appointment booking configuration fetched successfully.",
        )


class AppointmentBookingConfigUpdateAPIView(
    AppointmentBookingChatbotMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    serializer_class = AppointmentBookingConfigSerializer

    def _update(self, request, *, partial):
        config = self.get_config()
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
            module="appointment",
            action="appointment.config.updated",
            description="Updated appointment booking configuration.",
            metadata={
                "config_id": str(config.id),
                **activity_update_metadata(
                    previous, activity_serializer_snapshot(serializer, config, fields),
                ),
            },
        )
        return APIResponse.success(
            data=self.get_serializer(config).data,
            message="Appointment booking configuration updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class AppointmentBookingScheduleAPIView(
    AppointmentBookingChatbotMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    serializer_class = AppointmentBookingAvailabilitySerializer

    def get(self, request, *args, **kwargs):
        config = (
            AppointmentBookingConfig.objects.prefetch_related(
                "schedules__slots",
                "closed_dates",
            )
            .filter(chatbot=self.get_chatbot())
            .first()
        )
        if config is None:
            config = self.get_config()
        return APIResponse.success(
            data=self.get_serializer(config).data,
            message="Appointment booking schedules fetched successfully.",
        )


class AppointmentBookingScheduleUpdateAPIView(
    AppointmentBookingChatbotMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    serializer_class = AppointmentBookingAvailabilitySerializer

    def _update(self, request, *, partial):
        config = self.get_config()
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
            module="appointment",
            action="appointment.schedule.updated",
            description="Updated appointment booking schedules.",
            metadata={
                "config_id": str(config.id),
                **activity_update_metadata(
                    previous, activity_serializer_snapshot(serializer, config, fields),
                ),
            },
        )
        return APIResponse.success(
            data=self.get_serializer(config).data,
            message="Appointment booking schedules updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class AppointmentListAPIView(
    AppointmentBookingChatbotMixin,
    PaginatedAppointmentMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.APPOINTMENT_MANAGEMENT
    serializer_class = AppointmentSerializer

    def get(self, request, *args, **kwargs):
        appointments = Appointment.objects.filter(
            chatbot=self.get_chatbot()
        ).order_by("-starts_at")
        return self.paginated_response(
            appointments,
            message="Appointments fetched successfully.",
        )


class AppointmentStatsAPIView(AppointmentBookingChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.APPOINTMENT_MANAGEMENT
    chatbot_query_serializer_class = AppointmentStatsQuerySerializer

    def get(self, request, *args, **kwargs):
        query = self.get_chatbot_query()
        chatbot = self.get_chatbot()
        appointments = Appointment.objects.filter(chatbot=chatbot)
        chatbot_timezone = ZoneInfo(chatbot.timezone)
        start_date = query.get("start_date")
        end_date = query.get("end_date")
        if start_date:
            appointments = appointments.filter(
                starts_at__gte=timezone.make_aware(
                    datetime.combine(start_date, time.min), chatbot_timezone,
                ),
            )
        if end_date:
            appointments = appointments.filter(
                starts_at__lte=timezone.make_aware(
                    datetime.combine(end_date, time.max), chatbot_timezone,
                ),
            )
        counts = appointments.aggregate(
            total=Count("id"),
            booked=Count("id", filter=Q(status=AppointmentStatus.PENDING)),
            confirmed=Count("id", filter=Q(status=AppointmentStatus.CONFIRMED)),
            cancelled=Count("id", filter=Q(status=AppointmentStatus.CANCELLED)),
        )
        return APIResponse.success(
            data=counts,
            message="Appointment stats fetched successfully.",
        )


class AppointmentUpdateAPIView(AppointmentObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.APPOINTMENT_MANAGEMENT
    serializer_class = AppointmentUpdateSerializer

    def _update(self, request, *, partial):
        appointment = self.get_appointment()
        serializer = self.get_serializer(
            appointment,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        fields = tuple(serializer.validated_data)
        previous = activity_serializer_snapshot(serializer, appointment, fields)
        appointment = serializer.save()
        record_chatbot_activity(
            chatbot=appointment.chatbot,
            user=request.user,
            module="appointment",
            action="appointment.updated",
            description="Updated appointment.",
            metadata={
                "appointment_id": str(appointment.id),
                **activity_update_metadata(
                    previous, activity_serializer_snapshot(serializer, appointment, fields),
                ),
            },
        )
        return APIResponse.success(
            data=AppointmentSerializer(appointment).data,
            message="Appointment updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class AppointmentDeleteAPIView(AppointmentObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.APPOINTMENT_MANAGEMENT
    serializer_class = AppointmentSerializer

    def delete(self, request, *args, **kwargs):
        appointment = self.get_appointment()
        appointment_id = str(appointment.id)
        chatbot = appointment.chatbot
        snapshot = AppointmentSerializer(appointment).data
        appointment.delete()
        record_chatbot_activity(
            chatbot=chatbot,
            user=request.user,
            module="appointment",
            action="appointment.deleted",
            description="Deleted appointment.",
            metadata={"appointment_id": appointment_id, "appointment": snapshot},
        )
        return APIResponse.success(
            data={"id": appointment_id},
            message="Appointment deleted successfully.",
        )