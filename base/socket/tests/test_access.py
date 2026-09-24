from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase

from base.socket.services.access import dashboard_access, session_access
from base.socket.services.groups import workspace_dashboard_group, chatbot_dashboard_group
from chatbot.utils.choices import ChatbotPermissionTypes


class SocketAccessTests(SimpleTestCase):
    @patch("base.socket.services.access.ChatbotUser.objects")
    @patch("base.socket.services.access.WorkspaceUser.objects")
    def test_dashboard_groups_include_active_workspace_and_chatbot_memberships(
        self, workspaces, chatbots,
    ):
        user_id, workspace_id, chatbot_id = uuid4(), uuid4(), uuid4()
        workspaces.filter.return_value.values_list.return_value = [workspace_id]
        membership_id = uuid4()
        chatbots.filter.return_value.values_list.return_value = [
            (chatbot_id, membership_id)
        ]
        groups, workspace_ids, chatbot_memberships = dashboard_access(user_id)
        self.assertIn(workspace_dashboard_group(workspace_id), groups)
        self.assertIn(chatbot_dashboard_group(chatbot_id), groups)
        self.assertEqual(workspace_ids, {str(workspace_id)})
        self.assertEqual(
            chatbot_memberships,
            {str(chatbot_id): str(membership_id)},
        )
        workspaces.filter.assert_called_once_with(
            user_id=user_id, user__is_active=True, is_active=True, workspace__is_active=True,
        )
        chatbots.filter.assert_called_once_with(
            user_id=user_id, user__is_active=True, is_active=True,
            chatbot__is_deleted=False, chatbot__workspace__is_active=True,
        )
        chatbots.filter.return_value.values_list.assert_called_once_with(
            "chatbot_id", "id"
        )

    @patch("base.socket.services.access.ChatbotUser.objects")
    @patch("base.socket.services.access.WorkspaceUser.objects")
    def test_chatbot_only_member_gets_chatbot_group_and_presence_scope(
        self, workspaces, chatbots,
    ):
        user_id, chatbot_id, membership_id = uuid4(), uuid4(), uuid4()
        workspaces.filter.return_value.values_list.return_value = []
        chatbots.filter.return_value.values_list.return_value = [
            (chatbot_id, membership_id),
        ]

        groups, workspace_ids, chatbot_memberships = dashboard_access(user_id)

        self.assertEqual(workspace_ids, set())
        self.assertIn(chatbot_dashboard_group(chatbot_id), groups)
        self.assertEqual(
            chatbot_memberships,
            {str(chatbot_id): str(membership_id)},
        )

    @patch("base.socket.services.access.ChatbotUser.objects")
    @patch("base.socket.services.access.ChatSession.objects")
    def test_session_access_requires_chatbot_membership_and_management_permission(
        self,
        sessions,
        chatbots,
    ):
        user_id, session_id, workspace_id, chatbot_id = uuid4(), uuid4(), uuid4(), uuid4()
        session = SimpleNamespace(chatbot_id=chatbot_id, chatbot=SimpleNamespace(workspace_id=workspace_id))
        sessions.select_related.return_value.get.return_value = session
        agent = Mock()
        chatbots.select_related.return_value.get.return_value = agent
        agent.has_permission.return_value = False
        self.assertIsNone(session_access(user_id, session_id))
        agent.has_permission.assert_called_with(ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT)
        agent.has_permission.return_value = True
        self.assertEqual(session_access(user_id, session_id), (session, agent))
        chatbots.select_related.return_value.get.assert_called_with(
            chatbot_id=chatbot_id, user_id=user_id, is_active=True, user__is_active=True,
        )
        sessions.select_related.return_value.get.assert_called_with(
            pk=session_id,
            is_test=False,
            chatbot__is_deleted=False,
            chatbot__workspace__is_active=True,
        )

    @patch("base.socket.services.access.ChatSession.objects")
    def test_missing_session_and_malformed_id_do_not_grant_access(self, sessions):
        from chat.models import ChatSession
        for error in [ChatSession.DoesNotExist(), ValueError()]:
            sessions.select_related.return_value.get.side_effect = error
            self.assertIsNone(session_access(uuid4(), "bad-session"))
