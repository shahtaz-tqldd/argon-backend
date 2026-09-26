from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot
from chatbot.utils.choices import ChatbotStatusTypes
from workspace.models import Workspace

User = get_user_model()


class AdminChatbotAPITests(APITestCase):
    list_url = "/api/v1/admin/chatbots/list/"
    detail_url = "/api/v1/admin/chatbots/details/"
    update_url = "/api/v1/admin/chatbots/update/"
    delete_url = "/api/v1/admin/chatbots/delete/"

    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            email="superadmin-chatbots@example.com",
            password="StrongPass123!",
        )
        self.owner = User.objects.create_user(
            email="chatbot-owner@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Admin Chatbot Workspace",
            owner=self.owner,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Managed Bot",
            business_name="Managed Business",
            created_by=self.owner,
        )
        self.client.force_authenticate(self.superadmin)

    @staticmethod
    def chatbot_query(chatbot):
        return {"chatbot": chatbot.slug}

    def test_list_is_paginated_and_scoped_to_workspace(self):
        second_chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Second Managed Bot",
            created_by=self.owner,
        )
        deleted_chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Deleted Bot",
            created_by=self.owner,
            is_deleted=True,
        )
        other_owner = User.objects.create_user(
            email="other-chatbot-owner@example.com",
            password="StrongPass123!",
        )
        other_workspace = Workspace.objects.create(
            name="Other Admin Workspace",
            owner=other_owner,
        )
        Chatbot.objects.create(
            workspace=other_workspace,
            chatbot_name="Other Workspace Bot",
            created_by=other_owner,
        )

        response = self.client.get(
            self.list_url,
            {"workspace": self.workspace.slug, "page_size": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertIn(
            response.data["data"][0]["slug"],
            {self.chatbot.slug, second_chatbot.slug},
        )
        self.assertNotEqual(
            response.data["data"][0]["slug"],
            deleted_chatbot.slug,
        )
        self.assertEqual(
            response.data["data"][0]["workspace"]["slug"],
            self.workspace.slug,
        )

    def test_list_requires_an_active_workspace(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.workspace.is_active = False
        self.workspace.save(update_fields=["is_active", "updated_at"])
        response = self.client.get(
            self.list_url,
            {"workspace": self.workspace.slug},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_superadmin_can_fetch_chatbot_details(self):
        response = self.client.get(
            self.detail_url,
            self.chatbot_query(self.chatbot),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["id"], str(self.chatbot.id))
        self.assertEqual(
            response.data["data"]["workspace"]["slug"],
            self.workspace.slug,
        )
        self.assertEqual(
            response.data["data"]["created_by"]["email"],
            self.owner.email,
        )

    def test_superadmin_can_update_chatbot(self):
        response = self.client.patch(
            self.update_url,
            {
                "chatbot_name": "Updated Managed Bot",
                "description": "Updated by a superadmin.",
                "status": ChatbotStatusTypes.ACTIVE,
            },
            format="json",
            query_params=self.chatbot_query(self.chatbot),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.chatbot.refresh_from_db()
        self.assertEqual(self.chatbot.chatbot_name, "Updated Managed Bot")
        self.assertEqual(self.chatbot.status, ChatbotStatusTypes.ACTIVE)
        self.assertEqual(self.chatbot.updated_by, self.superadmin)
        self.assertEqual(
            response.data["data"]["description"],
            "Updated by a superadmin.",
        )

    def test_superadmin_can_soft_delete_chatbot(self):
        response = self.client.delete(
            self.delete_url,
            query_params=self.chatbot_query(self.chatbot),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.chatbot.refresh_from_db()
        self.assertTrue(self.chatbot.is_deleted)
        self.assertEqual(
            self.chatbot.status,
            ChatbotStatusTypes.DISABLED_BY_ADMIN,
        )
        self.assertEqual(self.chatbot.updated_by, self.superadmin)

        response = self.client.get(
            self.detail_url,
            self.chatbot_query(self.chatbot),
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_superadmin_cannot_access_admin_chatbot_endpoints(self):
        staff_user = User.objects.create_user(
            email="ordinary-staff@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        self.client.force_authenticate(staff_user)

        responses = (
            self.client.get(
                self.list_url,
                {"workspace": self.workspace.slug},
            ),
            self.client.get(
                self.detail_url,
                self.chatbot_query(self.chatbot),
            ),
            self.client.patch(
                self.update_url,
                {"description": "Forbidden"},
                format="json",
                query_params=self.chatbot_query(self.chatbot),
            ),
            self.client.delete(
                self.delete_url,
                query_params=self.chatbot_query(self.chatbot),
            ),
        )

        self.assertTrue(
            all(
                response.status_code == status.HTTP_403_FORBIDDEN
                for response in responses
            )
        )
