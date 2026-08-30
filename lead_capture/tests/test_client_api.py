from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot, ChatbotCapacity, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from lead_capture.models import Lead, LeadCaptureConfig, LeadNote
from subscription.choices import PlanFeature
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser

User = get_user_model()


class LeadCaptureClientAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="lead-api@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Lead API Workspace",
            slug="lead-api-workspace",
            owner=self.user,
        )
        WorkspaceUser.objects.create(
            workspace=self.workspace,
            user=self.user,
            role=WorkspaceRole.ADMIN,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Lead API Bot",
            slug="lead-api-bot",
            created_by=self.user,
        )
        self.chatbot_user = ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=self.user,
            role=ChatbotRoleTypes.ADMIN,
        )
        self.capacity = ChatbotCapacity.objects.create(
            chatbot=self.chatbot,
            active_features=[PlanFeature.LEAD_CAPTURE],
        )
        self.client.force_authenticate(self.user)

    def url(self, name, *, lead=None, note=None):
        query = f"?chatbot_slug={self.chatbot.slug}"
        if lead is not None:
            query += f"&lead_id={lead.id}"
        if note is not None:
            query += f"&note_id={note.id}"
        return f"{reverse(name)}{query}"

    def test_configuration_can_be_created_fetched_and_updated(self):
        response = self.client.post(
            self.url("lead-config-create"),
            {
                "is_enabled": True,
                "custom_fields": [
                    {
                        "label": "Organization",
                        "value": "organization",
                        "mode": "required",
                    },
                    {
                        "label": "Team Size",
                        "value": "team_size",
                        "mode": "optional",
                    },
                ],
                "intro_message": "Tell us about yourself.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        config = LeadCaptureConfig.objects.get(chatbot=self.chatbot)
        self.assertTrue(config.is_enabled)
        self.assertEqual(len(config.custom_fields), 2)

        response = self.client.get(self.url("lead-config"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["chatbot_id"], str(self.chatbot.id))

        response = self.client.patch(
            self.url("lead-config-update"),
            {"intro_message": "Updated introduction."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        config.refresh_from_db()
        self.assertEqual(config.intro_message, "Updated introduction.")

    def test_feature_is_required(self):
        self.capacity.active_features = []
        self.capacity.save(update_fields=["active_features", "updated_at"])

        response = self.client.get(self.url("lead-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_leads_are_paginated(self):
        for number in range(3):
            Lead.objects.create(
                chatbot=self.chatbot,
                name=f"Lead {number}",
                email=f"lead-{number}@example.com",
            )

        response = self.client.get(f'{self.url("lead-list")}&page_size=2')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        self.assertEqual(len(response.data["data"]), 2)
        self.assertIn("notes_count", response.data["data"][0])

    def test_lead_information_can_be_updated(self):
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            name="Original Name",
            email="original@example.com",
        )

        response = self.client.patch(
            self.url("lead-update", lead=lead),
            {
                "name": "Updated Name",
                "email": "UPDATED@EXAMPLE.COM",
                "status": "contacted",
                "lead_score": 75,
                "custom_fields": {"organization": "Argon"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lead.refresh_from_db()
        self.assertEqual(lead.name, "Updated Name")
        self.assertEqual(lead.email, "updated@example.com")
        self.assertEqual(lead.status, "contacted")
        self.assertEqual(lead.lead_score, 75)

    def test_notes_can_be_created_fetched_updated_listed_and_deleted(self):
        lead = Lead.objects.create(chatbot=self.chatbot, name="Noted Lead")

        response = self.client.post(
            self.url("lead-note-create", lead=lead),
            {"content": "Follow up tomorrow."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        note = LeadNote.objects.get(lead=lead)
        self.assertEqual(note.author, self.chatbot_user)
        self.assertEqual(note.content, "Follow up tomorrow.")

        response = self.client.get(
            self.url("lead-note-detail", lead=lead, note=note)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["id"], str(note.id))

        response = self.client.patch(
            self.url("lead-note-update", lead=lead, note=note),
            {"content": "Follow up next week."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        note.refresh_from_db()
        self.assertEqual(note.content, "Follow up next week.")

        response = self.client.get(
            self.url("lead-note-list", lead=lead)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)
        self.assertEqual(
            response.data["data"][0]["author"]["email"],
            self.user.email,
        )

        response = self.client.delete(
            self.url("lead-note-delete", lead=lead, note=note)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["id"], str(note.id))
        self.assertFalse(LeadNote.objects.filter(pk=note.id).exists())

    def test_note_detail_is_scoped_to_its_lead(self):
        lead = Lead.objects.create(chatbot=self.chatbot, name="First Lead")
        other_lead = Lead.objects.create(
            chatbot=self.chatbot,
            name="Second Lead",
        )
        note = LeadNote.objects.create(
            lead=lead,
            author=self.chatbot_user,
            content="Private to the first lead.",
        )

        response = self.client.get(
            self.url("lead-note-detail", lead=other_lead, note=note)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_task_endpoints_only_accept_their_own_http_methods(self):
        lead = Lead.objects.create(chatbot=self.chatbot, name="Method Lead")

        self.assertEqual(
            self.client.post(self.url("lead-config")).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.patch(
                self.url("lead-detail", lead=lead),
                {},
                format="json",
            ).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.post(
                self.url("lead-note-list", lead=lead),
                {"content": "Wrong endpoint."},
                format="json",
            ).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
