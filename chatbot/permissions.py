from rest_framework.permissions import BasePermission

from chatbot.models import ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from workspace.models import WorkspaceRole, WorkspaceUser


class IsChatbotUser(BasePermission):
    """Allow active chatbot users, with elevated delete access for admins."""

    message = "You are not an active member of this chatbot."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)

    def has_object_permission(self, request, view, obj):
        chatbot = getattr(obj, "chatbot", obj)
        user = request.user

        chatbot_membership = ChatbotUser.objects.filter(
            chatbot=chatbot,
            user=user,
            is_active=True,
        )
        workspace_membership = WorkspaceUser.objects.filter(
            workspace=chatbot.workspace,
            user=user,
            is_active=True,
        )
        if not workspace_membership.exists():
            return False
        workspace_admin = workspace_membership.filter(
            role=WorkspaceRole.ADMIN,
        ).exists()

        if request.method == "DELETE":
            chatbot_admin = chatbot_membership.filter(
                role=ChatbotRoleTypes.ADMIN,
            ).exists()
            if not (workspace_admin or chatbot_admin):
                self.message = (
                    "Only a workspace admin or chatbot admin can delete a chatbot."
                )
                return False
            return True

        if getattr(view, "allow_workspace_admin", False) and workspace_admin:
            return True

        return chatbot_membership.exists()
