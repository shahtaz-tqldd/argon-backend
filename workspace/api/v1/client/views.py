
from django.contrib.auth import get_user_model
from django.db.models import Case, IntegerField, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import GenericAPIView

from app.services.r2 import schedule_delete_image
from app.utils.pagination import CustomPagination
from app.utils.permission import IsWorkspaceOwner
from app.utils.response import APIResponse
from workspace.api.v1.client.serializers import (
    WorkspaceCreateSerializer,
    WorkspaceDeleteSerializer,
    WorkspaceDetailSerializer,
    WorkspaceListSerializer,
    WorkspaceQuerySerializer,
    WorkspaceUpdateSerializer,
)
from workspace.models import Workspace

User = get_user_model()


def first_error_message(errors, fallback="Request failed."):
    if isinstance(errors, dict):
        for value in errors.values():
            message = first_error_message(value, fallback="")
            if message:
                return message
        return fallback
    if isinstance(errors, (list, tuple)):
        for value in errors:
            message = first_error_message(value, fallback="")
            if message:
                return message
        return fallback
    return str(errors) if errors else fallback


def validation_error_response(errors, fallback):
    return APIResponse.error(
        errors=errors,
        message=first_error_message(errors, fallback=fallback),
        status=status.HTTP_400_BAD_REQUEST,
    )


class WorkspaceObjectMixin:
    _workspace = None

    def get_workspace(self):
        if self._workspace is None:
            query_serializer = WorkspaceQuerySerializer(
                data=self.request.query_params,
            )
            query_serializer.is_valid(raise_exception=True)
            self._workspace = get_object_or_404(
                Workspace.objects.select_related("owner").filter(
                    is_active=True,
                ),
                slug=query_serializer.validated_data["workspace"],
            )
            self.check_object_permissions(self.request, self._workspace)
        return self._workspace


class PaginatedListMixin:
    pagination_class = CustomPagination

    def paginated_response(self, records, *, message):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(records, self.request, view=self)
        serializer = self.get_serializer(page, many=True)
        return APIResponse.success(
            data=serializer.data,
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


class WorkspaceListView(PaginatedListMixin, GenericAPIView):
    permission_classes = [IsWorkspaceOwner]
    serializer_class = WorkspaceListSerializer

    def get_queryset(self):
        return (
            Workspace.objects.select_related("owner")
            .filter(
                owner=self.request.user,
                is_active=True,
            )
            .distinct()
            .order_by(
                Case(
                    When(owner=self.request.user, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                "-created_at",
            )
        )

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Workspaces fetched successfully.",
        )


class WorkspaceCreateView(GenericAPIView):
    permission_classes = [IsWorkspaceOwner]
    serializer_class = WorkspaceCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Workspace creation failed.",
            )
        try:
            workspace = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(
                exc.detail,
                "Workspace creation failed.",
            )
        return APIResponse.success(
            data=self.get_serializer(workspace).data,
            message="Workspace created successfully.",
            status=status.HTTP_201_CREATED,
        )


class WorkspaceDetailView(WorkspaceObjectMixin, GenericAPIView):
    permission_classes = [IsWorkspaceOwner]
    serializer_class = WorkspaceDetailSerializer

    def get_workspace(self):
        if "workspace" in self.request.query_params:
            return super().get_workspace()
        workspace = (
            Workspace.objects.select_related("owner")
            .filter(
                owner=self.request.user,
                is_active=True,
            )
            .distinct()
            .first()
        )
        if workspace is None:
            raise Http404
        self.check_object_permissions(self.request, workspace)
        return workspace

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_workspace()).data,
            message="Workspace fetched successfully.",
        )


class WorkspaceUpdateView(WorkspaceObjectMixin, GenericAPIView):
    permission_classes = [IsWorkspaceOwner]
    serializer_class = WorkspaceUpdateSerializer

    def _update(self, request, *, partial):
        workspace = self.get_workspace()
        if workspace.owner_id != request.user.id:
            return APIResponse.error(
                message=(
                    "Only the workspace owner can update workspace "
                    "information."
                ),
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(
            workspace,
            data=request.data,
            partial=partial,
        )
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Workspace update failed.",
            )
        workspace = serializer.save()
        return APIResponse.success(
            data=self.get_serializer(workspace).data,
            message="Workspace updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class WorkspaceDeleteView(WorkspaceObjectMixin, GenericAPIView):
    permission_classes = [IsWorkspaceOwner]
    serializer_class = WorkspaceDeleteSerializer

    def delete(self, request, *args, **kwargs):
        workspace = self.get_workspace()
        previous_logo_url = workspace.logo
        workspace.is_active = False
        workspace.logo = ""
        workspace.updated_by = request.user
        workspace.save(
            update_fields=["is_active", "logo", "updated_by", "updated_at"]
        )
        schedule_delete_image(image_url=previous_logo_url)
        return APIResponse.success(
            message="Workspace deleted successfully.",
        )

