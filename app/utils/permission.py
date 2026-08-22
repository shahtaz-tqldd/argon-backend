from rest_framework.permissions import BasePermission

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser


def _is_active_authenticated_user(user):
    return bool(user and user.is_authenticated and getattr(user, "is_active", False))


def _chatbot_from_object(obj):
    if isinstance(obj, Chatbot):
        return obj
    return getattr(obj, "chatbot", None)


def _workspace_from_object(obj):
    if isinstance(obj, Workspace):
        return obj

    workspace = getattr(obj, "workspace", None)
    if workspace is not None:
        return workspace

    chatbot = _chatbot_from_object(obj)
    return getattr(chatbot, "workspace", None)


def _active_membership_role(model, *, user, **scope):
    return (
        model.objects.filter(
            user=user,
            is_active=True,
            **scope,
        )
        .values_list("role", flat=True)
        .first()
    )


class IsAdmin(BasePermission):
    message = "Only staff users can perform this action."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and getattr(user, "is_staff", False))


class IsSuperAdmin(BasePermission):
    message = "Only superadmin users can perform this action."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "is_superuser", False)
        )


class IsWorkspaceUser(BasePermission):
    """Allow active users who have an active membership in the workspace."""

    message = "You are not an active member of this workspace."

    def has_permission(self, request, view):
        return _is_active_authenticated_user(request.user)

    def has_object_permission(self, request, view, obj):
        workspace = _workspace_from_object(obj)
        if workspace is None or not workspace.is_active:
            return False

        role = _active_membership_role(
            WorkspaceUser,
            workspace=workspace,
            user=request.user,
        )
        if role is None:
            return False

        if request.method == "DELETE" and role != WorkspaceRole.ADMIN:
            self.message = "Only a workspace admin can delete a workspace."
            return False

        if getattr(view, "workspace_admin_only", False):
            if role != WorkspaceRole.ADMIN:
                self.message = "Only a workspace admin can perform this action."
                return False

        return True


class IsChatbotUser(BasePermission):
    """Allow active chatbot members or authorized workspace administrators."""

    message = "You are not an active member of this chatbot."

    def has_permission(self, request, view):
        return _is_active_authenticated_user(request.user)

    def has_object_permission(self, request, view, obj):
        chatbot = _chatbot_from_object(obj)
        if (
            chatbot is None
            or chatbot.is_deleted
            or not chatbot.workspace.is_active
        ):
            return False

        workspace_role = _active_membership_role(
            WorkspaceUser,
            workspace=chatbot.workspace,
            user=request.user,
        )

        chatbot_membership = (
            ChatbotUser.objects.filter(
                chatbot=chatbot,
                user=request.user,
                is_active=True,
            )
            .first()
        )
        chatbot_role = (
            chatbot_membership.role if chatbot_membership is not None else None
        )

        if getattr(view, "chatbot_admin_only", False):
            if not (
                workspace_role == WorkspaceRole.ADMIN
                or chatbot_role == ChatbotRoleTypes.ADMIN
            ):
                self.message = (
                    "Only a workspace admin or chatbot admin can perform "
                    "this action."
                )
                return False
            return True

        if request.method == "DELETE":
            if not (
                workspace_role == WorkspaceRole.ADMIN
                or chatbot_role == ChatbotRoleTypes.ADMIN
            ):
                self.message = (
                    "Only a workspace admin or chatbot admin can delete a chatbot."
                )
                return False
            return True

        if (
            getattr(view, "allow_workspace_admin", False)
            and workspace_role == WorkspaceRole.ADMIN
        ):
            return True

        if chatbot_membership is None:
            return False

        required_permission = getattr(
            view,
            "required_chatbot_permission",
            None,
        )
        if (
            required_permission is not None
            and not chatbot_membership.has_permission(required_permission)
        ):
            self.message = "You do not have the required chatbot permission."
            return False

        return True
