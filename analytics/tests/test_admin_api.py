from datetime import datetime, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from analytics.choices import AIUsageType
from analytics.services.ai_usage import record_ai_usage
from chat.choices import ChatMessageSender
from chat.models import ChatMessage, ChatSession
from journals.models import Journal
from trips.choices import AgentMessageSender, TripStatus
from trips.models import Trip, TripConversationMessage, TripConversationSession


class AnalyticsAdminApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser("admin@example.com", "testpass123")
        self.user = User.objects.create_user("user@example.com", "testpass123")
        self.client.force_authenticate(self.admin)

    def _set_created_at(self, instance, value):
        instance.__class__.objects.filter(pk=instance.pk).update(created_at=value)

    def test_overview_returns_totals_and_ai_usage_count(self):
        now = timezone.now()
        old_user = User.objects.create_user("old@example.com", "testpass123")
        self._set_created_at(old_user, now - timedelta(days=45))

        completed_trip = Trip.objects.create(user=self.user, title="Done", status=TripStatus.COMPLETED)
        old_trip = Trip.objects.create(user=self.user, title="Old")
        self._set_created_at(old_trip, now - timedelta(days=45))
        Journal.objects.create(author=self.user, content="Current journal")

        chat_session = ChatSession.objects.create(user=self.user)
        ChatMessage.objects.create(
            session=chat_session,
            sender=ChatMessageSender.USER,
            content="Chat message",
        )
        trip_session = TripConversationSession.objects.create(trip=completed_trip, user=self.user)
        TripConversationMessage.objects.create(
            session=trip_session,
            sender=AgentMessageSender.AGENT,
            content="Trip message",
        )
        record_ai_usage(
            user=self.user,
            usage_type=AIUsageType.CHAT,
            cost=0.001,
            tokens=10,
        )
        record_ai_usage(
            user=self.user,
            trip=completed_trip,
            usage_type=AIUsageType.TRIP_CHAT,
            cost=0.002,
            tokens=20,
        )
        old_usage = record_ai_usage(
            user=self.user,
            trip=completed_trip,
            usage_type=AIUsageType.TRIP_PLANNING,
            cost=0.003,
            tokens=30,
        )
        self._set_created_at(old_usage, now - timedelta(days=45))

        response = self.client.get("/api/v1/admin/analytics/overview/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["users"]["total"], 3)
        self.assertEqual(response.data["data"]["trips"]["total_planned"], 2)
        self.assertEqual(response.data["data"]["trips"]["completed"], 1)
        self.assertEqual(response.data["data"]["journals"]["total"], 1)
        self.assertEqual(response.data["data"]["ai_messages"]["total"], 3)
        self.assertEqual(response.data["data"]["ai_messages"]["this_month"], 2)

    def test_ai_usage_returns_all_time_totals_and_type_breakdowns(self):
        trip = Trip.objects.create(user=self.user, title="Usage Trip")
        record_ai_usage(
            user=self.user,
            trip=trip,
            usage_type=AIUsageType.TRIP_PLANNING,
            cost=2.5,
            tokens=100,
        )
        record_ai_usage(
            user=self.user,
            usage_type=AIUsageType.CHAT,
            cost=1.25,
            tokens=50,
        )
        record_ai_usage(
            user=self.user,
            trip=trip,
            usage_type=AIUsageType.TRIP_CHAT,
            cost=0.75,
            tokens=25,
        )

        response = self.client.get("/api/v1/admin/analytics/ai-usage/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"],
            {
                "total_message": 3,
                "total_cost": 4.5,
                "total_tokens": 175,
                "trip_planning": {"cost": 2.5, "tokens": 100},
                "chat": {"cost": 1.25, "tokens": 50},
                "trip_chat": {"cost": 0.75, "tokens": 25},
            },
        )

    def test_ai_usage_filters_by_inclusive_date_range(self):
        first_usage = record_ai_usage(
            user=self.user,
            usage_type=AIUsageType.CHAT,
            cost=1,
            tokens=10,
        )
        included_usage = record_ai_usage(
            user=self.user,
            usage_type=AIUsageType.TRIP_CHAT,
            cost=2,
            tokens=20,
        )
        last_usage = record_ai_usage(
            user=self.user,
            usage_type=AIUsageType.TRIP_PLANNING,
            cost=4,
            tokens=40,
        )
        self._set_created_at(
            first_usage,
            timezone.make_aware(datetime(2026, 1, 9, 12)),
        )
        self._set_created_at(
            included_usage,
            timezone.make_aware(datetime(2026, 1, 31, 23, 59)),
        )
        self._set_created_at(
            last_usage,
            timezone.make_aware(datetime(2026, 2, 1, 0, 0)),
        )

        response = self.client.get(
            "/api/v1/admin/analytics/ai-usage/",
            {"start_date": "2026-01-10", "end_date": "2026-01-31"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["total_message"], 1)
        self.assertEqual(response.data["data"]["total_cost"], 2.0)
        self.assertEqual(response.data["data"]["total_tokens"], 20)
        self.assertEqual(
            response.data["data"]["trip_chat"],
            {"cost": 2.0, "tokens": 20},
        )

    def test_ai_usage_rejects_reversed_date_range(self):
        response = self.client.get(
            "/api/v1/admin/analytics/ai-usage/",
            {"start_date": "2026-02-01", "end_date": "2026-01-31"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end_date", response.data)

    def test_user_growth_returns_twelve_months_including_empty_months(self):
        response = self.client.get("/api/v1/admin/analytics/user-growth/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["granularity"], "month")
        self.assertEqual(len(response.data["data"]["points"]), 12)
        self.assertEqual(
            response.data["data"]["points"][-1]["period"],
            timezone.localdate().strftime("%Y-%m"),
        )

    def test_user_growth_month_filter_returns_every_day(self):
        response = self.client.get("/api/v1/admin/analytics/user-growth/", {"month": "2024-02"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["granularity"], "day")
        self.assertEqual(len(response.data["data"]["points"]), 29)
        self.assertEqual(response.data["data"]["points"][0]["period"], "2024-02-01")

    def test_analytics_requires_superadmin(self):
        self.client.force_authenticate(self.user)

        for url in (
            "/api/v1/admin/analytics/overview/",
            "/api/v1/admin/analytics/ai-usage/",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
