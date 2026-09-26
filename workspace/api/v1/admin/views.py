from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from app.services.r2 import schedule_delete_image
from app.utils.pagination import CustomPagination
from app.utils.permission import IsSuperAdmin
from app.utils.response import APIResponse
from workspace.api.v1.admin.serializers import (
    AdminWorkspaceDetailSerializer,
    AdminWorkspaceListSerializer,
    AdminWorkspaceQuerySerializer,
    AdminWorkspaceUpdateSerializer,
)
from workspace.models import Workspace


def workspace_admin_queryset():
    return (
        Workspace.objects.select_related(
            "owner",
            "created_by",
            "updated_by",
        )
        .annotate(
            member_count=Count(
                "memberships",
                filter=Q(memberships__is_active=True),
                distinct=True,
            ),
            chatbot_count=Count(
                "chatbots",
                filter=Q(chatbots__is_deleted=False),
                distinct=True,
            ),
        )
    )


class AdminWorkspaceObjectMixin:
    _workspace = None

    def get_workspace(self):
        if self._workspace is None:
            query_serializer = AdminWorkspaceQuerySerializer(
                data=self.request.query_params,
            )
            query_serializer.is_valid(raise_exception=True)
            self._workspace = get_object_or_404(
                workspace_admin_queryset(),
                slug=query_serializer.validated_data["workspace"],
                is_active=True,
            )
        return self._workspace


class AdminWorkspaceListAPIView(GenericAPIView):
    """Return all active workspaces to a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminWorkspaceListSerializer
    pagination_class = CustomPagination

    def get(self, request, *args, **kwargs):
        queryset = workspace_admin_queryset().filter(
            is_active=True,
        ).order_by("-created_at")
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
            message="Workspaces fetched successfully.",
        )


class AdminWorkspaceDetailView(AdminWorkspaceObjectMixin, GenericAPIView):
    """Return one active workspace to a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminWorkspaceDetailSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_workspace()).data,
            message="Workspace fetched successfully.",
        )


class AdminWorkspaceUpdateView(AdminWorkspaceObjectMixin, GenericAPIView):
    """Update one active workspace as a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminWorkspaceUpdateSerializer

    def _update(self, request, *, partial):
        serializer = self.get_serializer(
            self.get_workspace(),
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        workspace = serializer.save()
        return APIResponse.success(
            data=AdminWorkspaceDetailSerializer(
                workspace_admin_queryset().get(pk=workspace.pk),
                context=self.get_serializer_context(),
            ).data,
            message="Workspace updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class AdminWorkspaceDeleteView(AdminWorkspaceObjectMixin, GenericAPIView):
    """Soft-delete one workspace as a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def delete(self, request, *args, **kwargs):
        workspace = self.get_workspace()
        previous_logo_url = workspace.logo
        workspace.is_active = False
        workspace.logo = ""
        workspace.updated_by = request.user
        workspace.save(
            update_fields=[
                "is_active",
                "logo",
                "updated_by",
                "updated_at",
            ]
        )
        schedule_delete_image(image_url=previous_logo_url)
        return APIResponse.success(
            message="Workspace deleted successfully.",
        )
