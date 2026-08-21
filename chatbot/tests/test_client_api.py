from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import ChatbotInvitation, ChatbotUser
from chatbot.services import create_chatbot
from workspace.services import add_workspace_user, ensure_personal_workspace

User = get_user_model()


class ChatbotClientAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
        )
        self.member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
        )
        self.workspace = ensure_personal_workspace(self.owner)
        add_workspace_user(
            workspace=self.workspace,
            user=self.member,
            added_by=self.owner,
        )
        self.chatbot = create_chatbot(
            workspace=self.workspace,
            name="Support Bot",
            created_by=self.owner,
        )
        ChatbotUser.objects.create(chatbot=self.chatbot, user=self.member)
        self.client.force_authenticate(self.owner)

    def test_chatbot_detail_uses_chatbot_query_parameter(self):
        response = self.client.get(
            reverse("chatbot-detail"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["slug"], self.chatbot.slug)

    def test_chatbot_list_returns_page_metadata(self):
        create_chatbot(
            workspace=self.workspace,
            name="Sales Bot",
            created_by=self.owner,
        )

        response = self.client.get(
            reverse("chatbot-list"),
            {"page": 1, "page_size": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page"], 1)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(response.data["meta"]["num_pages"], 2)
        self.assertIsNotNone(response.data["meta"]["next"])
        self.assertIsNone(response.data["meta"]["previous"])

    def test_chatbot_member_list_returns_page_metadata(self):
        response = self.client.get(
            reverse("chatbot-members"),
            {
                "chatbot": self.chatbot.slug,
                "page": 1,
                "page_size": 1,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page"], 1)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(response.data["meta"]["num_pages"], 2)

    def test_chatbot_member_detail_uses_chatbot_and_email_query_parameters(self):
        response = self.client.get(
            reverse("chatbot-member-details"),
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"]["user"]["email"],
            self.member.email,
        )

    @patch("chatbot.api.v1.client.serializers.issue_chatbot_invitation")
    def test_invite_accepts_member_email_query_parameter(self, issue_invitation):
        invitation = ChatbotInvitation.objects.create(
            chatbot=self.chatbot,
            email=self.member.email,
            token_hash="test-token-hash",
            expires_at=timezone.now() + timedelta(hours=1),
            created_by=self.owner,
        )
        issue_invitation.return_value = invitation
        query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            }
        )

        response = self.client.post(
            f'{reverse("invite-chatbot-member")}?{query}',
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        issue_invitation.assert_called_once_with(
            chatbot=self.chatbot,
            email=self.member.email,
            invited_by=self.owner,
        )

    def test_remove_member_deletes_only_membership_and_marks_account_orphan(self):
        query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            }
        )
        response = self.client.delete(
            f'{reverse("remove-chatbot-member")}?{query}',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            ChatbotUser.objects.filter(
                chatbot=self.chatbot,
                user=self.member,
            ).exists()
        )
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_orphan)

    def test_member_detail_requires_both_query_parameters(self):
        response = self.client.get(
            reverse("chatbot-member-details"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("member_email", response.data)
