"""Authorization queries for socket delivery; clients never supply group names."""
from django.core.exceptions import ValidationError

from chatbot.models import ChatbotUser
from chatbot.utils.choices import ChatbotPermissionTypes
from chat.models import ChatSession
from workspace.models import WorkspaceUser
from base.socket.services.groups import (
    global_dashboard_group, user_dashboard_group,
    workspace_dashboard_group, chatbot_dashboard_group,
)


def dashboard_access(user_id):
    workspace_ids = list(WorkspaceUser.objects.filter(
        user_id=user_id, user__is_active=True, is_active=True,
        workspace__is_active=True,
    ).values_list("workspace_id", flat=True))
    chatbot_ids = list(ChatbotUser.objects.filter(
        user_id=user_id, user__is_active=True, is_active=True,
        chatbot__is_deleted=False, chatbot__workspace_id__in=workspace_ids,
    ).values_list("chatbot_id", flat=True))
    groups = {
        global_dashboard_group(), user_dashboard_group(user_id),
        *(workspace_dashboard_group(item) for item in workspace_ids),
        *(chatbot_dashboard_group(item) for item in chatbot_ids),
    }
    return groups, {str(item) for item in workspace_ids}


def session_access(user_id, session_id):
    try:
        session = ChatSession.objects.select_related("chatbot__workspace").get(
            pk=session_id, chatbot__is_deleted=False,
            chatbot__workspace__is_active=True,
        )
        if not WorkspaceUser.objects.filter(
            user_id=user_id, workspace_id=session.chatbot.workspace_id,
            is_active=True, user__is_active=True,
        ).exists():
            return None
        agent = ChatbotUser.objects.select_related("user", "chatbot").get(
            chatbot_id=session.chatbot_id, user_id=user_id,
            is_active=True, user__is_active=True,
        )
    except (ChatSession.DoesNotExist, ChatbotUser.DoesNotExist, ValidationError, ValueError):
        return None
    if not agent.has_permission(ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT):
        return None
    return session, agent
