from django.shortcuts import get_object_or_404
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from app.services.r2 import schedule_delete_image
from app.utils.pagination import CustomPagination
from app.utils.permission import IsSuperAdmin
from app.utils.response import APIResponse
from chatbot.api.v1.admin.serializers import (
    AdminChatbotDetailSerializer,
    AdminChatbotListQuerySerializer,
    AdminChatbotListSerializer,
    AdminChatbotQuerySerializer,
    AdminChatbotUpdateSerializer,
)
from chatbot.models import Chatbot
from chatbot.utils.choices import ChatbotStatusTypes
from workspace.models import Workspace


class AdminChatbotObjectMixin:
    _chatbot = None

    def get_chatbot(self):
        if self._chatbot is None:
            query_serializer = AdminChatbotQuerySerializer(
                data=self.request.query_params,
            )
            query_serializer.is_valid(raise_exception=True)
            self._chatbot = get_object_or_404(
                Chatbot.objects.select_related(
                    "workspace",
                    "created_by",
                    "updated_by",
                ),
                slug=query_serializer.validated_data["chatbot"],
                is_deleted=False,
                workspace__is_active=True,
            )
        return self._chatbot


class AdminChatbotListAPIView(GenericAPIView):
    """Return active chatbots belonging to one workspace."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminChatbotListSerializer
    pagination_class = CustomPagination

    def get(self, request, *args, **kwargs):
        query_serializer = AdminChatbotListQuerySerializer(
            data=request.query_params,
        )
        query_serializer.is_valid(raise_exception=True)
        workspace = get_object_or_404(
            Workspace,
            slug=query_serializer.validated_data["workspace"],
            is_active=True,
        )
        queryset = (
            Chatbot.objects.filter(
                workspace=workspace,
                is_deleted=False,
            )
            .select_related("workspace", "created_by")
            .order_by("-created_at")
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return APIResponse.success(
            data=self.get_serializer(page, many=True).data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Chatbots fetched successfully.",
        )


class AdminChatbotDetailView(AdminChatbotObjectMixin, GenericAPIView):
    """Return one active chatbot to a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminChatbotDetailSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chatbot()).data,
            message="Chatbot fetched successfully.",
        )


class AdminChatbotUpdateView(AdminChatbotObjectMixin, GenericAPIView):
    """Update one active chatbot as a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminChatbotUpdateSerializer

    def _update(self, request, *, partial):
        serializer = self.get_serializer(
            self.get_chatbot(),
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        chatbot = serializer.save()
        return APIResponse.success(
            data=AdminChatbotDetailSerializer(
                chatbot,
                context=self.get_serializer_context(),
            ).data,
            message="Chatbot updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class AdminChatbotDeleteView(AdminChatbotObjectMixin, GenericAPIView):
    """Soft-delete one chatbot as a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def delete(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        previous_logo_url = chatbot.logo
        chatbot.is_deleted = True
        chatbot.logo = ""
        chatbot.status = ChatbotStatusTypes.DISABLED_BY_ADMIN
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
