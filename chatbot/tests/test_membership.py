from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from chatbot.models import ChatbotUser, ChatbotWidgetSettings
from chatbot.services import assign_user_to_chatbot, create_chatbot
from chatbot.utils.choices import ChatbotRoleTypes
from workspace.services import add_workspace_user, ensure_personal_workspace

User = get_user_model()


class ChatbotMembershipTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
        )
        self.member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
        )
        self.other_member = User.objects.create_user(
            email="other-member@example.com",
            password="StrongPass123!",
        )
        self.outsider = User.objects.create_user(
            email="outsider@example.com",
            password="StrongPass123!",
        )
        self.workspace = ensure_personal_workspace(self.owner)
        add_workspace_user(
            workspace=self.workspace,
            user=self.member,
            added_by=self.owner,
        )
        add_workspace_user(
            workspace=self.workspace,
            user=self.other_member,
            added_by=self.owner,
        )

    def test_chatbot_creator_is_assigned_as_admin(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            name="Support Bot",
            created_by=self.owner,
        )

        membership = ChatbotUser.objects.get(
            chatbot=chatbot,
            user=self.owner,
        )
        self.assertEqual(membership.role, ChatbotRoleTypes.ADMIN)
        self.assertTrue(
            ChatbotWidgetSettings.objects.filter(chatbot=chatbot).exists()
        )

    def test_workspace_member_can_assign_another_workspace_member(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            name="Support Bot",
            created_by=self.owner,
        )

        membership = assign_user_to_chatbot(
            chatbot=chatbot,
            user=self.other_member,
            assigned_by=self.member,
        )

        self.assertEqual(membership.user, self.other_member)
        self.assertTrue(membership.is_active)

    def test_outsider_cannot_be_assigned_to_chatbot(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            name="Support Bot",
            created_by=self.owner,
        )

        with self.assertRaises(PermissionDenied):
            assign_user_to_chatbot(
                chatbot=chatbot,
                user=self.outsider,
                assigned_by=self.owner,
            )
