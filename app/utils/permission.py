from rest_framework.permissions import BasePermission

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from workspace.models import Workspace


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


class IsWorkspaceOwner(BasePermission):
    """Allow active users to act only on workspaces they own."""

    message = "You are not the owner of this workspace."

    def has_permission(self, request, view):
        return _is_active_authenticated_user(request.user)

    def has_object_permission(self, request, view, obj):
        workspace = _workspace_from_object(obj)
        if workspace is None or not workspace.is_active:
            return False

        if workspace.owner != request.user:
            return False

        return True


class IsChatbotUser(BasePermission):
    """Allow active chatbot members or the owning workspace's owner."""

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
            if chatbot_role != ChatbotRoleTypes.ADMIN:
                self.message = "Only a chatbot admin can perform this action."
                return False
            return True

        if request.method == "DELETE":
            if chatbot_role != ChatbotRoleTypes.ADMIN:
                self.message = (
                    "Only a chatbot admin can delete a chatbot."
                )
                return False
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
