from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from analytics.utils.choices import AIUsageType
from analytics.models import AIUsage
from chat.models import ChatMessage, ChatSession
from chat.utils.choices import ChatMessageSenderType
from chatbot.models import Chatbot, ChatbotConfig
from lead_capture.models import LeadAIInsight
from lead_capture.services.insights import (
    WeeklyLeadInsight,
    InsightIntent,
    InsightQuestion,
    InsightTopic,
    collect_visitor_messages,
    generate_weekly_lead_insight,
    last_week_window,
)
from subscription.utils.choices import (
    BillingInterval,
    PaymentProvider,
    PlanFeature,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription, PlanPrice, SubscriptionPlan
from workspace.models import Workspace


User = get_user_model()


def noon(value):
    """Noon local time on the given date."""
    return timezone.make_aware(
        datetime.combine(value, time(12, 0)),
        timezone.get_current_timezone(),
    )


class FakeGeminiClient:
    """Records prompts and returns a canned structured response."""

    def __init__(self, parsed):
        self.prompts = []
        self._parsed = parsed
        self.models = self
        self.usage_metadata = SimpleNamespace(
            prompt_token_count=1200,
            candidates_token_count=240,
            thoughts_token_count=0,
            cached_content_token_count=0,
            total_token_count=1440,
        )

    def generate_content(self, *, model, contents, config):
        self.prompts.append({"model": model, "prompt": contents, "config": config})
        return SimpleNamespace(
            parsed=self._parsed,
            text="",
            usage_metadata=self.usage_metadata,
        )


class WeeklyLeadInsightTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor == "sqlite":
            connection.ensure_connection()
            connection.connection.create_function(
                "jsonb_array_length",
                1,
                lambda value: len(__import__("json").loads(value)),
            )
            connection.connection.create_function(
                "jsonb_typeof",
                1,
                lambda value: (
                    "array"
                    if isinstance(__import__("json").loads(value), list)
                    else "object"
                    if isinstance(__import__("json").loads(value), dict)
                    else "scalar"
                ),
            )

    def setUp(self):
        self.owner = User.objects.create_user(
            email="insight-owner@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Insight Workspace",
            slug="insight-workspace",
            owner=self.owner,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Insight Bot",
            slug="insight-bot",
            created_by=self.owner,
        )
        today = timezone.localdate()
        self.week_start = today - timedelta(days=today.weekday() + 7)
        self.week_end = self.week_start + timedelta(days=6)

    def _message(self, sender_type, content, *, when=None):
        session, _created = ChatSession.objects.get_or_create(
            chatbot=self.chatbot,
            defaults={"channel": "web_widget"},
        )
        message = ChatMessage.objects.create(
            chat_session=session,
            sender_type=sender_type,
            content=content,
        )
        if when is not None:
            ChatMessage.objects.filter(pk=message.pk).update(created_at=when)
        return message

    def parsed_insight(self):
        return WeeklyLeadInsight(
            summary="Visitors focused on pricing and integrations this week.",
            topics=[
                InsightTopic(
                    topic="Integrations",
                    note="Slack and HubSync came up repeatedly.",
                )
            ],
            frequently_asked_questions=[
                InsightQuestion(
                    question="Does the Growth plan include Slack support?",
                    times_asked=3,
                )
            ],
            common_intents=[
                InsightIntent(intent="pricing"),
                InsightIntent(intent="integrations"),
            ]
        )

    def test_last_week_window_returns_monday_to_sunday(self):
        week_start, week_end = last_week_window(today=date(2026, 10, 6))

        self.assertEqual(week_start, date(2026, 9, 28))
        self.assertEqual(week_end, date(2026, 10, 4))

        monday_week_start, _ = last_week_window(today=date(2026, 10, 5))
        self.assertEqual(monday_week_start, date(2026, 9, 28))

    def test_chatbot_without_visitor_messages_is_skipped(self):
        insight = generate_weekly_lead_insight(
            self.chatbot,
            week_start=self.week_start,
            week_end=self.week_end,
            client=FakeGeminiClient(self.parsed_insight()),
        )

        self.assertIsNone(insight)
        self.assertFalse(LeadAIInsight.objects.exists())

    def test_weekly_insight_stores_structured_analysis(self):
        mid_week = noon(self.week_start + timedelta(days=2))
        self._message(
            ChatMessageSenderType.VISITOR,
            "Does the Growth plan include Slack support?",
            when=mid_week,
        )
        self._message(
            ChatMessageSenderType.VISITOR,
            "What does integrations cost for a team of 20?",
            when=mid_week + timedelta(hours=1),
        )
        self._message(
            ChatMessageSenderType.AI,
            "SECRET-AI-REPLY",
            when=mid_week + timedelta(minutes=5),
        )
        client = FakeGeminiClient(self.parsed_insight())

        insight = generate_weekly_lead_insight(
            self.chatbot,
            week_start=self.week_start,
            week_end=self.week_end,
            client=client,
        )

        self.assertIsNotNone(insight)
        self.assertEqual(insight.week_start, self.week_start)
        self.assertEqual(insight.week_end, self.week_end)
        self.assertEqual(insight.visitor_message_count, 2)
        self.assertEqual(insight.session_count, 1)
        self.assertIn("pricing", insight.summary)
        self.assertEqual(
            insight.topics[0]["topic"], "Integrations"
        )
        self.assertEqual(
            insight.frequently_asked_questions[0]["times_asked"], 3
        )
        self.assertEqual(insight.common_intents[1]["intent"], "integrations")
        self.assertEqual(
            insight.metadata["model"], client.prompts[0]["model"]
        )
        expected_cost = round(
            (
                1200 * settings.GEMINI_INPUT_COST_PER_MILLION
                + 240 * settings.GEMINI_OUTPUT_COST_PER_MILLION
            )
            / 1_000_000,
            8,
        )
        self.assertEqual(insight.metadata["cost"], expected_cost)
        self.assertEqual(
            insight.metadata["token_usage"],
            {
                "input_tokens": 1200,
                "output_tokens": 240,
                "thinking_tokens": 0,
                "cached_input_tokens": 0,
                "total_tokens": 1440,
            },
        )

        # Only visitor messages reach the model.
        prompt = client.prompts[0]["prompt"]
        self.assertIn("Slack support", prompt)
        self.assertNotIn("SECRET-AI-REPLY", prompt)

        usage = AIUsage.objects.get(
            chatbot=self.chatbot,
            usage_type=AIUsageType.LEAD_INSIGHT_GENERATION,
        )
        self.assertEqual(usage.tokens, 1440)
        self.assertEqual(float(usage.cost), expected_cost)

    def test_regenerating_the_same_week_updates_the_existing_row(self):
        mid_week = noon(self.week_start + timedelta(days=2))
        self._message(
            ChatMessageSenderType.VISITOR,
            "Can I book a demo?",
            when=mid_week,
        )

        first = generate_weekly_lead_insight(
            self.chatbot,
            week_start=self.week_start,
            week_end=self.week_end,
            client=FakeGeminiClient(self.parsed_insight()),
        )
        second = generate_weekly_lead_insight(
            self.chatbot,
            week_start=self.week_start,
            week_end=self.week_end,
            client=FakeGeminiClient(self.parsed_insight()),
        )

        self.assertEqual(LeadAIInsight.objects.count(), 1)
        self.assertEqual(first.id, second.id)

    def test_collect_visitor_messages_ignores_out_of_window_and_test_sessions(self):
        self._message(
            ChatMessageSenderType.VISITOR,
            "Inside window",
            when=noon(self.week_start),
        )
        test_session = ChatSession.objects.create(
            chatbot=self.chatbot,
            is_test=True,
        )
        ChatMessage.objects.create(
            chat_session=test_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Test session message",
        )
        self._message(
            ChatMessageSenderType.VISITOR,
            "Outside window",
            when=noon(self.week_end + timedelta(days=8)),
        )

        messages = collect_visitor_messages(
            self.chatbot,
            week_start=self.week_start,
            week_end=self.week_end,
        )

        self.assertEqual(
            [message.content for message in messages],
            ["Inside window"],
        )


class WeeklyLeadInsightTaskTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="insight-task-owner@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Insight Task Workspace",
            slug="insight-task-workspace",
            owner=self.owner,
        )
        self.chatbots = []
        for name in ("Task Bot A", "Task Bot B", "Task Bot C", "Task Bot D"):
            self.chatbots.append(
                Chatbot.objects.create(
                    workspace=self.workspace,
                    chatbot_name=name,
                    slug=name.lower().replace(" ", "-"),
                    created_by=self.owner,
                )
            )
        # A, B, and D subscribe to plans with lead insights; C does not.
        for chatbot in (self.chatbots[0], self.chatbots[1], self.chatbots[3]):
            self.subscribe(chatbot, features=[PlanFeature.LEAD_INSIGHTS])
        self.subscribe(
            self.chatbots[2],
            features=[PlanFeature.LEAD_CAPTURE],
        )

    def subscribe(self, chatbot, *, features):
        ChatbotConfig.objects.create(chatbot=chatbot)
        plan = SubscriptionPlan.objects.create(
            name=f"Insight Task Plan {chatbot.slug}",
            ai_message_limit=100,
            file_size_limit_mb=10,
            knowledge_chunk_limit=30,
            features=features,
        )
        price = PlanPrice.objects.create(
            plan=plan,
            provider=PaymentProvider.MANUAL,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("0.00"),
        )
        return ChatbotSubscription.objects.create(
            chatbot=chatbot,
            plan_price=price,
            selected_by=self.owner,
            provider=PaymentProvider.MANUAL,
            renewal_mode=RenewalMode.MANUAL,
            status=SubscriptionStatus.ACTIVE,
        )

    def test_task_buckets_chatbots_by_outcome(self):
        from unittest.mock import patch

        from lead_capture import tasks as lead_tasks

        chatbots = list(
            Chatbot.objects.order_by("chatbot_name")
        )
        insight = LeadAIInsight(chatbot=chatbots[0], week_start=date(2026, 9, 28))
        insight.save()

        def fake_generate(chatbot, *, week_start, week_end):
            if "Task Bot A" in chatbot.chatbot_name:
                return insight
            if "Task Bot B" in chatbot.chatbot_name:
                return None
            raise RuntimeError("gemini unavailable")

        with patch.object(
            lead_tasks,
            "generate_weekly_lead_insight",
            side_effect=fake_generate,
        ) as generate:
            result = lead_tasks.generate_weekly_lead_ai_insights()

        self.assertEqual(
            result["generated"], [chatbots[0].slug]
        )
        self.assertEqual(
            result["skipped_no_messages"], [chatbots[1].slug]
        )
        self.assertEqual(
            result["failed"], [chatbots[3].slug]
        )
        # Bot C lacks the plan feature and never reaches generation.
        self.assertEqual(result["skipped_no_feature"], [chatbots[2].slug])
        self.assertEqual(generate.call_count, 3)
        self.assertEqual(
            date.fromisoformat(result["week_start"]).weekday(),
            0,
        )

    def test_task_skips_chatbots_without_insights_feature(self):
        from unittest.mock import patch

        from lead_capture import tasks as lead_tasks

        with patch.object(
            lead_tasks,
            "generate_weekly_lead_insight",
            return_value=None,
        ) as generate:
            result = lead_tasks.generate_weekly_lead_ai_insights()

        self.assertEqual(result["generated"], [])
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["skipped_no_feature"], [])
        self.assertEqual(
            result["skipped_no_messages"],
            [chatbot.slug for chatbot in self.chatbots[:2]]
            + [self.chatbots[3].slug],
        )
        self.assertEqual(generate.call_count, 3)
