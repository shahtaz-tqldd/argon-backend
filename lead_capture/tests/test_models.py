import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from lead_capture.utils.choices import LeadCaptureFieldMode
from lead_capture.models import Lead, LeadCaptureConfig, LeadNote
from workspace.models import Workspace

User = get_user_model()


def custom_fields(count):
    return [
        {
            "label": f"Custom Field {number}",
            "value": f"custom_field_{number}",
            "mode": LeadCaptureFieldMode.OPTIONAL,
        }
        for number in range(1, count + 1)
    ]


class LeadCaptureModelTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor == "sqlite":
            connection.ensure_connection()
            connection.connection.create_function(
                "jsonb_array_length",
                1,
                lambda value: len(json.loads(value)),
            )
            connection.connection.create_function(
                "jsonb_typeof",
                1,
                lambda value: (
                    "array" if isinstance(json.loads(value), list) else "object"
                    if isinstance(json.loads(value), dict) else "scalar"
                ),
            )

    def setUp(self):
        self.owner = User.objects.create_user(
            email="lead-owner@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Lead Workspace",
            slug="lead-workspace",
            owner=self.owner,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Lead Bot",
            created_by=self.owner,
        )
        self.chatbot_user = ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=self.owner,
            role=ChatbotRoleTypes.ADMIN,
        )

    def test_custom_field_config_accepts_expected_shape(self):
        config = LeadCaptureConfig(
            chatbot=self.chatbot,
            custom_fields=[
                {
                    "label": "Organization",
                    "value": "organization",
                    "mode": LeadCaptureFieldMode.REQUIRED,
                },
                {
                    "label": "Team Size",
                    "value": "team_size",
                    "mode": LeadCaptureFieldMode.OPTIONAL,
                },
            ],
        )

        config.full_clean()

    def test_custom_field_config_rejects_invalid_definitions(self):
        invalid_custom_fields = (
            [{"label": "Name", "value": "name", "mode": "required"}],
            [{"label": "Team Size", "value": "Team Size", "mode": "optional"}],
            [{"label": "Hidden", "value": "hidden_field", "mode": "hidden"}],
            [
                {"label": "Team Size", "value": "team_size", "mode": "optional"},
                {"label": "Staff", "value": "team_size", "mode": "required"},
            ],
        )

        for field_config in invalid_custom_fields:
            with self.subTest(field_config=field_config):
                config = LeadCaptureConfig(
                    chatbot=self.chatbot,
                    custom_fields=field_config,
                )
                with self.assertRaises(ValidationError):
                    config.full_clean()

    def test_enabled_config_can_collect_only_custom_fields(self):
        config = LeadCaptureConfig(
            chatbot=self.chatbot,
            is_enabled=True,
            name_mode=LeadCaptureFieldMode.HIDDEN,
            email_mode=LeadCaptureFieldMode.HIDDEN,
            phone_mode=LeadCaptureFieldMode.HIDDEN,
            address_mode=LeadCaptureFieldMode.HIDDEN,
            custom_fields=[
                {
                    "label": "Organization",
                    "value": "organization",
                    "mode": LeadCaptureFieldMode.REQUIRED,
                }
            ],
        )

        config.full_clean()
        config.save()

    def test_no_more_than_ten_total_fields_can_be_configured(self):
        config = LeadCaptureConfig(
            chatbot=self.chatbot,
            custom_fields=custom_fields(7),
        )
        config.full_clean()
        config.save()

        config.custom_fields = custom_fields(8)
        with self.assertRaises(ValidationError):
            config.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            LeadCaptureConfig.objects.filter(pk=config.pk).update(
                custom_fields=custom_fields(8),
            )

    def test_a_chatbot_has_at_most_one_lead_capture_config(self):
        LeadCaptureConfig.objects.create(chatbot=self.chatbot)

        with self.assertRaises(IntegrityError), transaction.atomic():
            LeadCaptureConfig.objects.create(chatbot=self.chatbot)

    def test_lead_normalizes_email_and_stores_custom_values(self):
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            name="Ada Lovelace",
            email="  ADA@Example.COM ",
            custom_fields={"organization": "Analytical Engines"},
            initial_ip_address="192.0.2.10",
            last_ip_address="2001:db8::1",
        )

        self.assertEqual(lead.email, "ada@example.com")
        self.assertEqual(
            lead.custom_fields,
            {"organization": "Analytical Engines"},
        )

    def test_lead_score_cannot_exceed_one_hundred(self):
        lead = Lead(chatbot=self.chatbot, lead_score=101)

        with self.assertRaises(ValidationError):
            lead.full_clean()

    def test_chatbot_user_can_create_a_note_for_its_lead(self):
        lead = Lead.objects.create(chatbot=self.chatbot)
        note = LeadNote(
            lead=lead,
            author=self.chatbot_user,
            content="Follow up next week.",
        )

        note.full_clean()
        note.save()

        self.assertEqual(lead.notes.get(), note)

    def test_user_from_another_chatbot_cannot_author_a_note(self):
        other_chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Other Bot",
            created_by=self.owner,
        )
        other_chatbot_user = ChatbotUser.objects.create(
            chatbot=other_chatbot,
            user=self.owner,
            role=ChatbotRoleTypes.ADMIN,
        )
        lead = Lead.objects.create(chatbot=self.chatbot)
        note = LeadNote(
            lead=lead,
            author=other_chatbot_user,
            content="This should not be allowed.",
        )

        with self.assertRaises(ValidationError) as error:
            note.full_clean()

        self.assertIn("author", error.exception.message_dict)
