from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from analytics.choices import AIUsageType
from analytics.models import AIUsage
from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from chat.models import ChatMessage, ChatSession
from chat.utils.choices import ChatMessageSenderType
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser

User = get_user_model()


class AdminAnalyticsAPITests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            email="analytics-superadmin@example.com",
            password="StrongPass123!",
        )
        self.owner = User.objects.create_user(
            email="analytics-owner@example.com",
            password="StrongPass123!",
            is_email_verified=True,
        )
        self.workspace = Workspace.objects.create(
            name="Analytics Workspace",
            owner=self.owner,
            created_by=self.owner,
        )
        WorkspaceUser.objects.create(
            workspace=self.workspace,
            user=self.owner,
            role=WorkspaceRole.ADMIN,
            created_by=self.owner,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Analytics Bot",
            created_by=self.owner,
        )
        ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=self.owner,
            role=ChatbotRoleTypes.ADMIN,
        )

        other_owner = User.objects.create_user(
            email="other-analytics-owner@example.com",
            password="StrongPass123!",
        )
        self.other_workspace = Workspace.objects.create(
            name="Other Analytics Workspace",
            owner=other_owner,
            created_by=other_owner,
        )
        WorkspaceUser.objects.create(
            workspace=self.other_workspace,
            user=other_owner,
            role=WorkspaceRole.ADMIN,
            created_by=other_owner,
        )
        self.other_chatbot = Chatbot.objects.create(
            workspace=self.other_workspace,
            chatbot_name="Other Analytics Bot",
            created_by=other_owner,
        )
        ChatbotUser.objects.create(
            chatbot=self.other_chatbot,
            user=other_owner,
            role=ChatbotRoleTypes.ADMIN,
        )
        self.client.force_authenticate(self.superadmin)

    def test_ai_usage_is_scoped_and_has_no_legacy_trip_categories(self):
        AIUsage.objects.create(
            chatbot=self.chatbot,
            usage_type=AIUsageType.CHAT,
            cost=Decimal("0.01000000"),
            tokens=100,
            input_tokens=60,
            output_tokens=40,
            model="gpt-test",
        )
        AIUsage.objects.create(
            chatbot=self.chatbot,
            usage_type=AIUsageType.CONTENT_GENERATION,
            cost=Decimal("0.02000000"),
            tokens=200,
            input_tokens=120,
            output_tokens=80,
            model="gpt-test",
        )
        AIUsage.objects.create(
            chatbot=self.other_chatbot,
            usage_type=AIUsageType.CHAT,
            cost=Decimal("9.00000000"),
            tokens=9000,
            model="other-model",
        )

        response = self.client.get(
            reverse("analytics-ai-usage"),
            {
                "chatbot": self.chatbot.slug,
                "granularity": "day",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["totals"]["requests"], 2)
        self.assertEqual(data["totals"]["cost"], 0.03)
        self.assertEqual(data["totals"]["tokens"], 300)
        self.assertEqual(
            {item["usage_type"] for item in data["by_usage_type"]},
            {"chat", "content_generation"},
        )
        self.assertNotIn("trip_chat", data)
        self.assertEqual(data["by_model"][0]["model"], "gpt-test")

    def test_platform_analytics_is_scoped_to_workspace(self):
        response = self.client.get(
            reverse("analytics-platform"),
            {"workspace": self.workspace.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["users"]["total"], 1)
        self.assertEqual(data["users"]["email_verified"], 1)
        self.assertEqual(data["workspaces"]["total"], 1)
        self.assertEqual(data["chatbots"]["total"], 1)
        self.assertEqual(data["filters"]["workspace"], self.workspace.slug)

    def test_growth_combines_users_workspaces_and_chatbots(self):
        today = timezone.localdate().isoformat()

        response = self.client.get(
            reverse("analytics-growth"),
            {
                "workspace": self.workspace.slug,
                "start_date": today,
                "end_date": today,
                "granularity": "day",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(
            data["totals"],
            {"users": 1, "workspaces": 1, "chatbots": 1},
        )
        self.assertEqual(
            data["points"],
            [
                {
                    "period": today,
                    "users": 1,
                    "workspaces": 1,
                    "chatbots": 1,
                }
            ],
        )

    def test_conversation_analytics_excludes_test_sessions(self):
        session = ChatSession.objects.create(
            chatbot=self.chatbot,
            visitor_id="analytics-visitor",
        )
        ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Question",
        )
        ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.AI,
            content="Answer",
        )
        test_session = ChatSession.objects.create(
            chatbot=self.chatbot,
            is_test=True,
        )
        ChatMessage.objects.create(
            chat_session=test_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Test question",
        )
        other_session = ChatSession.objects.create(
            chatbot=self.other_chatbot,
            visitor_id="other-analytics-visitor",
        )
        ChatMessage.objects.create(
            chat_session=other_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Other question",
        )

        response = self.client.get(
            reverse("analytics-conversations"),
            {
                "chatbot": self.chatbot.slug,
                "granularity": "day",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["totals"]["sessions"], 1)
        self.assertEqual(data["totals"]["messages"], 2)
        self.assertEqual(
            {
                item["sender_type"]: item["count"]
                for item in data["messages_by_sender"]
            },
            {"visitor": 1, "ai": 1, "agent": 0, "system": 0},
        )

    def test_filters_validate_date_order_and_scope_consistency(self):
        response = self.client.get(
            reverse("analytics-platform"),
            {"start_date": "2026-09-25", "end_date": "2026-09-24"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end_date", response.data["errors"])

        response = self.client.get(
            reverse("analytics-platform"),
            {
                "workspace": self.workspace.slug,
                "chatbot": self.other_chatbot.slug,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("chatbot", response.data["errors"])

    def test_non_superadmin_cannot_access_analytics(self):
        self.client.force_authenticate(self.owner)
        urls = (
            reverse("analytics-ai-usage"),
            reverse("analytics-platform"),
            reverse("analytics-growth"),
            reverse("analytics-conversations"),
        )

        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
