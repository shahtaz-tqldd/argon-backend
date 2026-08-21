from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from accounts.api.v1.client.serializers import (
    UserSerializer,
    build_auth_token_payload,
)
from app.utils.permission import IsWorkspaceUser
from app.utils.response import APIResponse
from workspace.api.v1.client.serializers import (
    AcceptWorkspaceInvitationSerializer,
    InviteWorkspaceMemberSerializer,
    WorkspaceInvitationSerializer,
    WorkspaceSerializer,
)
from workspace.models import Workspace


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


class WorkspaceDetailView(GenericAPIView):
    permission_classes = [IsWorkspaceUser]
    serializer_class = WorkspaceSerializer

    def get_object(self):
        queryset = (
            Workspace.objects.select_related("owner")
            .filter(
                Q(owner=self.request.user)
                | Q(
                    memberships__user=self.request.user,
                    memberships__is_active=True,
                ),
                is_active=True,
            )
            .distinct()
        )
        workspace_slug = self.kwargs.get("workspace_slug")
        if workspace_slug:
            workspace = get_object_or_404(queryset, slug=workspace_slug)
        else:
            workspace = queryset.filter(owner=self.request.user).order_by(
                "created_at"
            ).first()
            if workspace is None:
                workspace = queryset.order_by("created_at").first()
            if workspace is None:
                raise Http404
        self.check_object_permissions(self.request, workspace)
        return workspace

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_object()).data,
            message="Workspace fetched successfully.",
        )

    def patch(self, request, *args, **kwargs):
        workspace = self.get_object()
        if workspace.owner_id != request.user.id:
            return APIResponse.error(
                message="Only the workspace owner can update workspace information.",
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(
            workspace,
            data=request.data,
            partial=True,
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


class InviteWorkspaceMemberView(GenericAPIView):
    permission_classes = [IsWorkspaceUser]
    serializer_class = InviteWorkspaceMemberSerializer

    def get_workspace(self):
        queryset = Workspace.objects.select_related("owner").filter(is_active=True)
        workspace_slug = self.kwargs.get("workspace_slug")
        if workspace_slug:
            workspace = get_object_or_404(queryset, slug=workspace_slug)
        else:
            workspace = queryset.filter(owner=self.request.user).order_by(
                "created_at"
            ).first()
            if workspace is None:
                raise Http404
        self.check_object_permissions(self.request, workspace)
        return workspace

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["workspace"] = self.get_workspace()
        return context

    def post(self, request, *args, **kwargs):
        workspace = self.get_workspace()
        if workspace.owner_id != request.user.id:
            return APIResponse.error(
                message="Only the workspace owner can invite members.",
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Workspace invitation failed.",
            )
        try:
            invitation = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(
                exc.detail,
                "Workspace invitation failed.",
            )
        return APIResponse.success(
            data=WorkspaceInvitationSerializer(invitation).data,
            message="Workspace invitation sent successfully.",
            status=status.HTTP_201_CREATED,
        )


class AcceptWorkspaceInvitationView(GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = AcceptWorkspaceInvitationSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Invitation acceptance failed.",
            )
        try:
            user, membership = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(
                exc.detail,
                "Invitation acceptance failed.",
            )

        return APIResponse.success(
            data={
                "user": UserSerializer(user).data,
                "workspace": WorkspaceSerializer(membership.workspace).data,
                "tokens": build_auth_token_payload(user),
            },
            message="Account created and workspace joined successfully.",
            status=status.HTTP_201_CREATED,
        )
