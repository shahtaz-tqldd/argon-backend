from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import ChatbotActivityLog, ChatbotUser
from chatbot.services import record_chatbot_activity
from chatbot.services.membership import create_chatbot
from workspace.services import add_workspace_user, ensure_personal_workspace


User = get_user_model()


class ChatbotActivityLogTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
            name="Owner",
        )
        self.member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
            name="Member",
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
        self.chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )
        ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=self.member,
        )
        self.url = reverse("chatbot-activity-log-list")

    def test_record_chatbot_activity_creates_a_log(self):
        activity = record_chatbot_activity(
            chatbot=self.chatbot,
            user=self.owner,
            action="chatbot.updated",
            description="Updated the chatbot instructions.",
            metadata={"field": "instructions"},
        )

        self.assertEqual(activity.chatbot, self.chatbot)
        self.assertEqual(activity.user, self.owner)
        self.assertEqual(activity.action, "chatbot.updated")
        self.assertEqual(activity.metadata, {"field": "instructions"})

    def test_record_chatbot_activity_validates_input(self):
        with self.assertRaises(ValidationError):
            record_chatbot_activity(
                chatbot=self.chatbot,
                action="  ",
            )

        with self.assertRaises(ValidationError):
            record_chatbot_activity(
                chatbot=self.chatbot,
                action="chatbot.updated",
                metadata=["not", "an", "object"],
            )

    def test_list_returns_paginated_chatbot_activity(self):
        record_chatbot_activity(
            chatbot=self.chatbot,
            user=self.owner,
            action="chatbot.created",
        )
        record_chatbot_activity(
            chatbot=self.chatbot,
            user=self.member,
            action="knowledge.uploaded",
            metadata={"filename": "faq.pdf"},
        )
        self.client.force_authenticate(self.owner)

        response = self.client.get(
            self.url,
            {"chatbot": self.chatbot.slug, "page_size": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(
            response.data["data"][0]["action"],
            "knowledge.uploaded",
        )
        self.assertEqual(
            response.data["data"][0]["user"]["email"],
            self.member.email,
        )

    def test_list_can_be_filtered_by_chatbot_user(self):
        record_chatbot_activity(
            chatbot=self.chatbot,
            user=self.owner,
            action="chatbot.updated",
        )
        record_chatbot_activity(
            chatbot=self.chatbot,
            user=self.member,
            action="knowledge.uploaded",
        )
        self.client.force_authenticate(self.owner)

        response = self.client.get(
            self.url,
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email.upper(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)
        self.assertEqual(
            response.data["data"][0]["user"]["email"],
            self.member.email,
        )

    def test_list_does_not_include_another_chatbots_activity(self):
        another_chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Sales Bot",
            created_by=self.owner,
        )
        record_chatbot_activity(
            chatbot=another_chatbot,
            user=self.owner,
            action="chatbot.updated",
        )
        self.client.force_authenticate(self.owner)

        response = self.client.get(
            self.url,
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 0)

    def test_list_rejects_a_non_member(self):
        ChatbotActivityLog.objects.create(
            chatbot=self.chatbot,
            user=self.owner,
            action="chatbot.updated",
        )
        self.client.force_authenticate(self.outsider)

        response = self.client.get(
            self.url,
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
