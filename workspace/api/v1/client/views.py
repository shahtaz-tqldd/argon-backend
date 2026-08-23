from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from accounts.api.v1.client.serializers import (
    UserSerializer,
    build_auth_token_payload,
)
from app.utils.pagination import CustomPagination
from app.utils.permission import IsWorkspaceUser
from app.utils.response import APIResponse
from workspace.api.v1.client.serializers import (
    AcceptWorkspaceInvitationSerializer,
    InviteWorkspaceMemberSerializer,
    WorkspaceCreateSerializer,
    WorkspaceDeleteSerializer,
    WorkspaceDetailSerializer,
    WorkspaceInvitationSerializer,
    WorkspaceListSerializer,
    WorkspaceMemberListSerializer,
    WorkspaceMemberQuerySerializer,
    WorkspaceMemberRoleUpdateSerializer,
    WorkspaceMemberSerializer,
    WorkspaceQuerySerializer,
    WorkspaceUpdateSerializer,
)
from workspace.models import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceRole,
    WorkspaceUser,
)

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


class WorkspaceMemberObjectMixin(WorkspaceObjectMixin):
    _workspace_member = None

    def get_workspace_member(self):
        if self._workspace_member is None:
            query_serializer = WorkspaceMemberQuerySerializer(
                data=self.request.query_params,
            )
            query_serializer.is_valid(raise_exception=True)
            self._workspace_member = get_object_or_404(
                WorkspaceUser.objects.select_related(
                    "workspace",
                    "user__profile",
                ),
                workspace=self.get_workspace(),
                user__email__iexact=(
                    query_serializer.validated_data["member_email"]
                ),
                user__is_active=True,
                is_active=True,
            )
            invitation = WorkspaceInvitation.objects.filter(
                workspace=self._workspace_member.workspace,
                email__iexact=self._workspace_member.user.email,
            ).first()
            self._workspace_member.invited_at = (
                invitation.created_at
                if invitation is not None
                else self._workspace_member.created_at
            )
            self.check_object_permissions(
                self.request,
                self._workspace_member,
            )
        return self._workspace_member


class WorkspaceListView(PaginatedListMixin, GenericAPIView):
    permission_classes = [IsWorkspaceUser]
    serializer_class = WorkspaceListSerializer

    def get_queryset(self):
        return (
            Workspace.objects.select_related("owner")
            .filter(
                memberships__user=self.request.user,
                memberships__is_active=True,
                is_active=True,
            )
            .distinct()
            .order_by("-created_at")
        )

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Workspaces fetched successfully.",
        )


class WorkspaceCreateView(GenericAPIView):
    permission_classes = [IsWorkspaceUser]
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


class WorkspaceDetailView(GenericAPIView):
    permission_classes = [IsWorkspaceUser]
    serializer_class = WorkspaceDetailSerializer

    def get_workspace(self):
        workspace = (
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
    permission_classes = [IsWorkspaceUser]
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
    permission_classes = [IsWorkspaceUser]
    serializer_class = WorkspaceDeleteSerializer

    def delete(self, request, *args, **kwargs):
        workspace = self.get_workspace()
        workspace.is_active = False
        workspace.updated_by = request.user
        workspace.save(
            update_fields=["is_active", "updated_by", "updated_at"]
        )
        return APIResponse.success(
            message="Workspace deleted successfully.",
        )


class WorkspaceMemberListView(
    WorkspaceObjectMixin,
    PaginatedListMixin,
    GenericAPIView,
):
    permission_classes = [IsWorkspaceUser]
    serializer_class = WorkspaceMemberListSerializer

    def get_records(self):
        workspace = self.get_workspace()
        memberships = list(
            WorkspaceUser.objects.filter(
                workspace=workspace,
                is_active=True,
                user__is_active=True,
            ).select_related("workspace", "user__profile")
        )
        invitations = list(
            WorkspaceInvitation.objects.filter(
                workspace=workspace,
                accepted_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).order_by("-created_at")
        )

        all_invitations = {
            invitation.email.casefold(): invitation
            for invitation in WorkspaceInvitation.objects.filter(
                workspace=workspace,
            )
        }
        active_emails = {
            membership.user.email.casefold() for membership in memberships
        }
        for membership in memberships:
            invitation = all_invitations.get(
                membership.user.email.casefold()
            )
            membership.invited_at = (
                invitation.created_at
                if invitation is not None
                else membership.created_at
            )

        pending_invitations = [
            invitation
            for invitation in invitations
            if invitation.email.casefold() not in active_emails
        ]
        for invitation in pending_invitations:
            invitation.user = SimpleNamespace(
                email=invitation.email,
                name="",
                profile=SimpleNamespace(avatar_url=""),
                last_active=None,
                last_login=None,
            )
            invitation.role = WorkspaceRole.MEMBER
            invitation.is_active = False
            invitation.invited_at = invitation.created_at

        return sorted(
            [*memberships, *pending_invitations],
            key=lambda record: (record.invited_at, str(record.id)),
            reverse=True,
        )

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_records(),
            message="Workspace members fetched successfully.",
        )


class WorkspaceMemberDetailView(
    WorkspaceMemberObjectMixin,
    GenericAPIView,
):
    permission_classes = [IsWorkspaceUser]
    serializer_class = WorkspaceMemberSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_workspace_member()).data,
            message="Workspace member fetched successfully.",
        )


class WorkspaceMemberRoleView(
    WorkspaceMemberObjectMixin,
    GenericAPIView,
):
    permission_classes = [IsWorkspaceUser]
    serializer_class = WorkspaceMemberRoleUpdateSerializer
    workspace_admin_only = True

    def role_data(self, membership):
        return WorkspaceMemberSerializer(
            membership,
            context=self.get_serializer_context(),
        ).data

    def get(self, request, *args, **kwargs):
        membership = self.get_workspace_member()
        return APIResponse.success(
            data=self.role_data(membership),
            message="Workspace member role fetched successfully.",
        )

    def patch(self, request, *args, **kwargs):
        membership = self.get_workspace_member()
        serializer = self.get_serializer(
            membership,
            data=request.data,
        )
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Workspace member role update failed.",
            )
        membership = serializer.save(updated_by=request.user)
        return APIResponse.success(
            data=self.role_data(membership),
            message="Workspace member role updated successfully.",
        )


class RemoveWorkspaceMemberView(
    WorkspaceMemberObjectMixin,
    GenericAPIView,
):
    permission_classes = [IsWorkspaceUser]
    serializer_class = WorkspaceMemberSerializer
    workspace_admin_only = True

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        membership = self.get_workspace_member()
        if membership.workspace.owner_id == membership.user_id:
            return APIResponse.error(
                message="The workspace owner cannot be removed.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        membership.is_active = False
        membership.updated_by = request.user
        membership.save(
            update_fields=["is_active", "updated_by", "updated_at"]
        )
        return APIResponse.success(
            data={"member_email": membership.user.email},
            message="Workspace member removed successfully.",
        )


class InviteWorkspaceMemberView(WorkspaceObjectMixin, GenericAPIView):
    permission_classes = [IsWorkspaceUser]
    serializer_class = InviteWorkspaceMemberSerializer

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
        serializer_data = request.data.copy()
        member_email = request.query_params.get("member_email")
        if member_email is not None:
            serializer_data["email"] = member_email
        serializer = self.get_serializer(data=serializer_data)
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
                "workspace": WorkspaceDetailSerializer(
                    membership.workspace,
                    context={"request": request},
                ).data,
                "tokens": build_auth_token_payload(user),
            },
            message="Account created and workspace joined successfully.",
            status=status.HTTP_201_CREATED,
        )
