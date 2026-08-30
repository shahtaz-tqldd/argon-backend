from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from chatbot.models import Chatbot
from lead_capture.models import Lead
from workspace.models import Workspace

User = get_user_model()


class CreateDemoLeadsCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="demo-leads@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Demo Leads Workspace",
            owner=self.user,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Demo Leads Bot",
            created_by=self.user,
        )

    def test_command_creates_repeatable_demo_leads(self):
        output = StringIO()

        call_command(
            "create_demo_leads",
            chatbot_slug=self.chatbot.slug,
            count=4,
            seed=42,
            stdout=output,
        )

        leads = Lead.objects.filter(chatbot=self.chatbot, source="demo")
        self.assertEqual(leads.count(), 4)
        first_snapshot = list(
            leads.order_by("email").values_list(
                "email",
                "name",
                "status",
                "lead_score",
            )
        )
        self.assertTrue(all(lead.custom_fields for lead in leads))
        self.assertIn("4 created, 0 updated", output.getvalue())

        output = StringIO()
        call_command(
            "create_demo_leads",
            chatbot_slug=self.chatbot.slug,
            count=4,
            seed=42,
            stdout=output,
        )

        self.assertEqual(leads.count(), 4)
        self.assertEqual(
            list(
                leads.order_by("email").values_list(
                    "email",
                    "name",
                    "status",
                    "lead_score",
                )
            ),
            first_snapshot,
        )
        self.assertIn("0 created, 4 updated", output.getvalue())

    def test_command_rejects_invalid_count(self):
        with self.assertRaisesMessage(
            CommandError,
            "--count must be at least 1.",
        ):
            call_command(
                "create_demo_leads",
                chatbot_slug=self.chatbot.slug,
                count=0,
            )

    def test_command_rejects_unknown_chatbot(self):
        with self.assertRaisesMessage(
            CommandError,
            "Active chatbot not found: missing-chatbot",
        ):
            call_command(
                "create_demo_leads",
                chatbot_slug="missing-chatbot",
            )
