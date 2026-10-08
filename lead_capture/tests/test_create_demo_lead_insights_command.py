from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from chatbot.models import Chatbot
from lead_capture.models import LeadAIInsight
from workspace.models import Workspace


User = get_user_model()


class CreateDemoLeadInsightsCommandTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="demo-insight-owner@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Demo Insight Workspace",
            slug="demo-insight-workspace",
            owner=self.owner,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Demo Insight Bot",
            slug="demo-insight-bot",
            created_by=self.owner,
        )

    def test_creates_one_insight_per_past_week(self):
        call_command(
            "create_demo_lead_insights",
            "--bot-id",
            self.chatbot.slug,
            "--count",
            "3",
        )

        insights = LeadAIInsight.objects.order_by("-week_start")
        self.assertEqual(insights.count(), 3)
        today = timezone.localdate()
        this_monday = today - timedelta(days=today.weekday())
        self.assertEqual(
            [insight.week_start for insight in insights],
            [
                this_monday - timedelta(days=7),
                this_monday - timedelta(days=14),
                this_monday - timedelta(days=21),
            ],
        )
        for insight in insights:
            self.assertEqual(insight.week_end - insight.week_start, timedelta(days=6))
            self.assertEqual(insight.week_start.weekday(), 0)
            self.assertTrue(insight.summary)
            self.assertTrue(insight.topics)
            self.assertTrue(insight.frequently_asked_questions)
            self.assertTrue(insight.common_intents)
            self.assertEqual(insight.metadata["model"], "demo")
            self.assertEqual(insight.metadata["cost"], 0.00096)
            self.assertEqual(
                insight.metadata["token_usage"]["total_tokens"], 1440
            )
            self.assertGreaterEqual(insight.visitor_message_count, 5)

    def test_rerun_updates_the_same_rows(self):
        call_command(
            "create_demo_lead_insights",
            "--bot-id",
            str(self.chatbot.id),
            "--count",
            "2",
        )
        existing_ids = set(
            LeadAIInsight.objects.values_list("id", flat=True)
        )

        call_command(
            "create_demo_lead_insights",
            "--bot-id",
            self.chatbot.slug,
            "--count",
            "2",
        )

        self.assertEqual(LeadAIInsight.objects.count(), 2)
        self.assertEqual(
            set(LeadAIInsight.objects.values_list("id", flat=True)),
            existing_ids,
        )

    def test_unknown_chatbot_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command(
                "create_demo_lead_insights",
                "--bot-id",
                "missing-bot",
            )
