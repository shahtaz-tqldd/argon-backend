from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView

from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from chatbot.models import Chatbot, ChatbotCapacity, ChatbotUser
from chatbot.services.capacity import get_chatbot_capacity
from chatbot.utils.choices import ChatbotPermissionTypes
from lead_capture.api.v1.client.serializers import (
    LeadCaptureConfigSerializer,
    LeadChatbotQuerySerializer,
    LeadNoteQuerySerializer,
    LeadNoteSerializer,
    LeadQuerySerializer,
    LeadSerializer,
    LeadUpdateSerializer,
)
from lead_capture.models import Lead, LeadCaptureConfig, LeadNote
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
            except ChatbotCapacity.DoesNotExist as exc:
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
                Lead.objects.select_related("chatbot", "chatbot__workspace"),
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
        config = serializer.save()
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


class LeadDetailView(LeadObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.LEAD_MANAGEMENT
    serializer_class = LeadSerializer

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
        serializer = self.get_serializer(
            self.get_lead(),
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        lead = serializer.save()
        return APIResponse.success(
            data=LeadSerializer(lead).data,
            message="Lead updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


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
        serializer = self.get_serializer(
            self.get_lead_note(),
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        note = serializer.save()
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
        note.delete()
        return APIResponse.success(
            data={"id": note_id},
            message="Lead note deleted successfully.",
        )
