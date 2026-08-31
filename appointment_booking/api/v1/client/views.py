from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView

from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from appointment_booking.api.v1.client.serializers import (
    AppointmentBookingAvailabilitySerializer,
    AppointmentBookingConfigSerializer,
    AppointmentChatbotQuerySerializer,
    AppointmentQuerySerializer,
    AppointmentSerializer,
    AppointmentUpdateSerializer,
)
from appointment_booking.models import Appointment, AppointmentBookingConfig
from chatbot.models import Chatbot, ChatbotCapacity
from chatbot.services.capacity import get_chatbot_capacity
from chatbot.utils.choices import ChatbotPermissionTypes
from subscription.choices import PlanFeature


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
            except ChatbotCapacity.DoesNotExist as exc:
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


class AppointmentBookingConfigView(
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


class AppointmentBookingConfigUpdateView(
    AppointmentBookingChatbotMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    serializer_class = AppointmentBookingConfigSerializer

    def _update(self, request, *, partial):
        serializer = self.get_serializer(
            self.get_config(),
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        config = serializer.save()
        return APIResponse.success(
            data=self.get_serializer(config).data,
            message="Appointment booking configuration updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class AppointmentBookingScheduleView(
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


class AppointmentBookingScheduleUpdateView(
    AppointmentBookingChatbotMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    serializer_class = AppointmentBookingAvailabilitySerializer

    def _update(self, request, *, partial):
        serializer = self.get_serializer(
            self.get_config(),
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        config = serializer.save()
        return APIResponse.success(
            data=self.get_serializer(config).data,
            message="Appointment booking schedules updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class AppointmentListView(
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


class AppointmentUpdateView(AppointmentObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.APPOINTMENT_MANAGEMENT
    serializer_class = AppointmentUpdateSerializer

    def _update(self, request, *, partial):
        serializer = self.get_serializer(
            self.get_appointment(),
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        appointment = serializer.save()
        return APIResponse.success(
            data=AppointmentSerializer(appointment).data,
            message="Appointment updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class AppointmentDeleteView(AppointmentObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.APPOINTMENT_MANAGEMENT
    serializer_class = AppointmentSerializer

    def delete(self, request, *args, **kwargs):
        appointment = self.get_appointment()
        appointment_id = str(appointment.id)
        appointment.delete()
        return APIResponse.success(
            data={"id": appointment_id},
            message="Appointment deleted successfully.",
        )
