import csv
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework import status
from rest_framework.test import APITestCase

from chat.models import ChatMessage, ChatSession
from chat.utils.choices import ChatMessageSenderType
from chatbot.models import Chatbot, ChatbotConfig, ChatbotUser, ChatbotVisitor
from chatbot.utils.choices import ChatbotRoleTypes
from lead_capture.models import Lead, LeadAIInsight, LeadCaptureConfig, LeadNote, LeadSignal
from lead_capture.services.signals import record_lead_signal
from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    PlanFeature,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription, PlanPrice, SubscriptionPlan
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
        self.capacity = ChatbotConfig.objects.create(
            chatbot=self.chatbot,
        )
        plan = SubscriptionPlan.objects.create(
            name="Lead Plan",
            ai_message_limit=100,
            file_size_limit_mb=10,
            knowledge_chunk_limit=30,
            features=[PlanFeature.LEAD_CAPTURE],
        )
        price = PlanPrice.objects.create(
            plan=plan,
            provider=PaymentProvider.MANUAL,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("0.00"),
        )
        ChatbotSubscription.objects.create(
            chatbot=self.chatbot,
            plan_price=price,
            selected_by=self.user,
            provider=PaymentProvider.MANUAL,
            renewal_mode=RenewalMode.MANUAL,
            status=SubscriptionStatus.ACTIVE,
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
                "collectable_fields": [
                    {
                        "label": "Organization",
                        "value": "organization",
                        "mode": "required",
                        "type": "text",
                    },
                    {
                        "label": "Team Size",
                        "value": "team_size",
                        "mode": "optional",
                        "type": "text",
                    },
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        config = LeadCaptureConfig.objects.get(chatbot=self.chatbot)
        self.assertTrue(config.is_enabled)
        self.assertEqual(len(config.collectable_fields), 2)

        response = self.client.get(self.url("lead-config"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["chatbot_id"], str(self.chatbot.id))

        response = self.client.patch(
            self.url("lead-config-update"),
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        config.refresh_from_db()

    def test_feature_is_required(self):
        with patch.object(
            ChatbotConfig,
            "has_feature",
            return_value=False,
        ):
            response = self.client.get(self.url("lead-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_leads_are_paginated(self):
        for number in range(3):
            Lead.objects.create(
                chatbot=self.chatbot,
                collected_fields={
                    "name": f"Lead {number}",
                    "email": f"lead-{number}@example.com",
                },
            )

        response = self.client.get(f'{self.url("lead-list")}&page_size=2')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        self.assertEqual(len(response.data["data"]), 2)
        self.assertIn("notes_count", response.data["data"][0])

    def test_lead_details_include_visitor(self):
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={
                "name": "Visited Lead",
                "email": "visited-lead@example.com",
            },
        )
        visitor = ChatbotVisitor.objects.create(
            chatbot=self.chatbot,
            visitor_id="detail-visitor-1",
            lead=lead,
            ip_address="203.0.113.9",
            detected_location="Dhaka, BD",
            detected_country="BD",
            metadata={"browser": "Chrome 129"},
        )

        response = self.client.get(self.url("lead-detail", lead=lead))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        visitor_data = response.data["data"]["visitor"]
        self.assertEqual(visitor_data["id"], str(visitor.id))
        self.assertEqual(visitor_data["visitor_id"], "detail-visitor-1")
        self.assertEqual(visitor_data["detected_location"], "Dhaka, BD")
        self.assertEqual(visitor_data["detected_country"], "BD")
        self.assertEqual(visitor_data["metadata"], {"browser": "Chrome 129"})

    def test_lead_details_without_visitor_serializes_null(self):
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={
                "name": "Orphan Lead",
                "email": "orphan-lead@example.com",
            },
        )

        response = self.client.get(self.url("lead-detail", lead=lead))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["data"]["visitor"])

    def test_all_leads_can_be_exported_as_csv_without_a_date_range(self):
        Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={
                "name": "CSV Lead",
                "email": "csv@example.com",
            },
            initial_ip_address="192.0.2.10",
            detected_country_code="US",
            detected_city="New York",
            source="widget",
        )

        response = self.client.get(
            f'{self.url("export-lead-data")}&file_format=csv'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response["X-Lead-Export-Limit"], "1000")
        self.assertEqual(response["X-Lead-Export-Count"], "1")
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertIn("name", rows[0])
        self.assertIn("email", rows[0])
        self.assertEqual(rows[1][rows[0].index("name")], "CSV Lead")
        self.assertEqual(
            rows[1][rows[0].index("email")],
            "csv@example.com",
        )

    def test_lead_export_filters_by_inclusive_date_range(self):
        old_lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Old Lead"},
        )
        current_lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Current Lead"},
        )
        Lead.objects.filter(pk=old_lead.pk).update(
            created_at=timezone.now() - timedelta(days=5),
        )
        today = timezone.localdate().isoformat()

        response = self.client.get(
            f'{self.url("export-lead-data")}&file_format=csv'
            f"&start_date={today}&end_date={today}"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(response["X-Lead-Export-Count"], "1")
        self.assertEqual(
            rows[1][rows[0].index("name")],
            current_lead.collected_fields["name"],
        )

    def test_leads_can_be_exported_as_excel(self):
        Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Excel Lead"},
        )

        response = self.client.get(
            f'{self.url("export-lead-data")}&file_format=excel'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response["Content-Type"],
            (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        self.assertIn(".xlsx", response["Content-Disposition"])
        workbook = load_workbook(BytesIO(response.content), read_only=True)
        rows = list(workbook["Leads"].iter_rows(values_only=True))
        self.assertIn("name", rows[0])
        self.assertEqual(rows[1][rows[0].index("name")], "Excel Lead")

    def test_lead_export_has_a_hard_limit_of_1000(self):
        Lead.objects.bulk_create(
            [
                Lead(
                    chatbot=self.chatbot,
                    collected_fields={"name": f"Lead {index}"},
                )
                for index in range(1001)
            ]
        )

        response = self.client.get(
            f'{self.url("export-lead-data")}&file_format=csv'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(response["X-Lead-Export-Limit"], "1000")
        self.assertEqual(response["X-Lead-Export-Count"], "1000")
        self.assertEqual(len(rows), 1001)

    def test_lead_export_validates_format_and_date_order(self):
        response = self.client.get(
            f'{self.url("export-lead-data")}&file_format=pdf'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.get(
            f'{self.url("export-lead-data")}&file_format=csv'
            "&start_date=2026-09-25&end_date=2026-09-24"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end_date", response.data["errors"])

    def test_lead_information_can_be_updated(self):
        LeadCaptureConfig.objects.create(
            chatbot=self.chatbot,
            collectable_fields=[
                {
                    "label": "Name",
                    "value": "name",
                    "mode": "required",
                    "type": "text",
                },
                {
                    "label": "Email",
                    "value": "email",
                    "mode": "required",
                    "type": "email",
                },
                {
                    "label": "Organization",
                    "value": "organization",
                    "mode": "optional",
                    "type": "text",
                },
            ],
        )
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={
                "name": "Original Name",
                "email": "original@example.com",
            },
        )

        response = self.client.patch(
            self.url("lead-update", lead=lead),
            {
                "collected_fields": {
                    "name": "Updated Name",
                    "email": "UPDATED@EXAMPLE.COM",
                    "organization": "Argon",
                },
                "status": "contacted",
                "lead_score": 75,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lead.refresh_from_db()
        self.assertEqual(lead.collected_fields["name"], "Updated Name")
        self.assertEqual(lead.collected_fields["email"], "updated@example.com")
        self.assertEqual(lead.status, "contacted")
        self.assertEqual(lead.lead_score, 75)

    def test_lead_signal_history_is_paginated(self):
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={
                "name": "Signal Lead",
                "email": "signal-lead@example.com",
            },
        )
        other_lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={
                "name": "Other Lead",
                "email": "other-lead@example.com",
            },
        )
        chat_session = ChatSession.objects.create(chatbot=self.chatbot)
        message = ChatMessage.objects.create(
            chat_session=chat_session,
            sender_type=ChatMessageSenderType.AI,
            content="Reply with a score.",
        )
        record_lead_signal(lead, score=70, intent="Early interest.")
        record_lead_signal(lead, score=85, intent="Asked for pricing.")
        record_lead_signal(lead, score=95, intent="Requested a demo.", message=message)
        record_lead_signal(other_lead, score=10, intent="Other lead signal.")

        response = self.client.get(
            f'{self.url("lead-signal-list", lead=lead)}&page_size=2'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        self.assertEqual(response.data["meta"]["num_pages"], 2)
        self.assertEqual(len(response.data["data"]), 2)
        self.assertEqual(
            [item["score"] for item in response.data["data"]],
            [95, 85],
        )
        self.assertEqual(
            response.data["data"][0]["message_id"], str(message.id)
        )
        self.assertIsNone(response.data["data"][1]["message_id"])

    def test_lead_signal_history_requires_lead_scope(self):
        url = (
            f'{reverse("lead-signal-list")}'
            f'?chatbot_slug={self.chatbot.slug}'
            "&lead_id=00000000-0000-0000-0000-000000000000"
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_lead_signal_list_returns_signals_with_messages(self):
        session = ChatSession.objects.create(chatbot=self.chatbot)
        message = ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Please book me a demo for our team.",
        )
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Signal Lead", "email": "signal@example.com"},
        )
        record_lead_signal(lead, score=90, intent="Asked for pricing.")
        record_lead_signal(
            lead,
            score=95,
            intent="Requested a demo.",
            message=message,
        )
        old_signal_lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Old Signal", "email": "old-signal@example.com"},
        )
        old_signal = record_lead_signal(
            old_signal_lead, score=60, intent="Mild interest."
        )
        LeadSignal.objects.filter(pk=old_signal.pk).update(
            created_at=timezone.now() - timedelta(days=5),
        )

        response = self.client.get(
            f'{self.url("lead-signal-leads")}&page_size=2'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        self.assertEqual(len(response.data["data"]), 2)
        self.assertEqual(response.data["data"][0]["score"], 95)
        self.assertEqual(response.data["data"][0]["lead_id"], str(lead.id))
        self.assertEqual(
            response.data["data"][0]["message"]["content"],
            "Please book me a demo for our team.",
        )
        self.assertEqual(
            response.data["data"][0]["message"]["chat_session_id"],
            str(session.id),
        )
        self.assertIsNone(response.data["data"][1]["message"])

        today = timezone.localdate().isoformat()
        response = self.client.get(
            f'{self.url("lead-signal-leads")}&page_size=10'
            f"&start_date={today}&end_date={today}"
        )
        self.assertEqual(response.data["meta"]["count"], 2)

    def test_lead_signal_stats_separate_scores_with_optional_date_range(self):
        Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Hot", "email": "hot@example.com"},
            avg_score=Decimal("90.00"),
        )
        Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Medium", "email": "medium@example.com"},
            avg_score=Decimal("60.00"),
        )
        Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Low", "email": "low@example.com"},
            avg_score=Decimal("30.00"),
        )
        Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Unscored", "email": "unscored@example.com"},
        )
        old_hot_lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Old Hot", "email": "old-hot@example.com"},
            avg_score=Decimal("95.00"),
        )
        Lead.objects.filter(pk=old_hot_lead.pk).update(
            created_at=timezone.now() - timedelta(days=5),
        )

        response = self.client.get(self.url("lead-signal-stats"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["total_leads"], 5)
        self.assertEqual(data["hot_leads"], 2)
        self.assertEqual(data["medium_leads"], 1)
        self.assertEqual(data["low_leads"], 1)
        self.assertEqual(data["unscored_leads"], 1)
        self.assertEqual(data["average_lead_score"], 68.75)

        today = timezone.localdate().isoformat()
        response = self.client.get(
            f'{self.url("lead-signal-stats")}'
            f"&start_date={today}&end_date={today}"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["total_leads"], 4)
        self.assertEqual(data["hot_leads"], 1)
        self.assertEqual(data["medium_leads"], 1)
        self.assertEqual(data["low_leads"], 1)
        self.assertEqual(data["average_lead_score"], 60.0)

    def test_lead_signal_apis_validate_date_order(self):
        today = timezone.localdate().isoformat()
        response = self.client.get(
            f'{self.url("lead-signal-stats")}'
            f"&start_date={today}&end_date=2020-01-01"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_lead_growth_defaults_to_fifteen_daily_buckets(self):
        def make_lead(name, email):
            return Lead.objects.create(
                chatbot=self.chatbot,
                collected_fields={"name": name, "email": email},
            )

        today_lead_1 = make_lead("Today One", "today-one@example.com")
        today_lead_2 = make_lead("Today Two", "today-two@example.com")
        old_lead = make_lead("Sixteen Days", "sixteen-days@example.com")
        Lead.objects.filter(pk=old_lead.pk).update(
            created_at=timezone.now() - timedelta(days=16),
        )

        response = self.client.get(self.url("lead-growth"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        today = timezone.localdate().isoformat()
        self.assertEqual(data["interval"], "day")
        self.assertEqual(data["end_date"], today)
        self.assertEqual(data["total_leads"], 2)
        self.assertEqual(len(data["growth"]), 15)
        self.assertEqual(data["growth"][-1]["date"], today)
        self.assertEqual(data["growth"][-1]["count"], 2)
        self.assertEqual(data["growth"][-1]["cumulative_total"], 2)
        self.assertTrue(
            all(item["count"] == 0 for item in data["growth"][:-1])
        )

    def test_lead_growth_groups_monthly_within_a_date_range(self):
        def make_lead(name, created_at):
            lead = Lead.objects.create(
                chatbot=self.chatbot,
                collected_fields={"name": name, "email": f"{name}@example.com"},
            )
            Lead.objects.filter(pk=lead.pk).update(created_at=created_at)
            return lead

        today = timezone.localdate()
        make_lead("Forty Days", timezone.now() - timedelta(days=40))
        make_lead("Ten Days", timezone.now() - timedelta(days=10))
        make_lead("Today", timezone.now())
        start = today - timedelta(days=60)

        response = self.client.get(
            f'{self.url("lead-growth")}&interval=month'
            f"&start_date={start.isoformat()}&end_date={today.isoformat()}"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["interval"], "month")
        self.assertEqual(data["total_leads"], 3)
        expected_months = (
            (start.year * 12 + start.month,
             today.year * 12 + today.month + 1)
        )
        self.assertEqual(len(data["growth"]), expected_months[1] - expected_months[0])
        counts_by_date = {item["date"]: item["count"] for item in data["growth"]}
        forty_days_month = (today - timedelta(days=40)).replace(day=1).isoformat()
        ten_days_month = (today - timedelta(days=10)).replace(day=1).isoformat()
        today_month = today.replace(day=1).isoformat()
        self.assertEqual(counts_by_date[forty_days_month], 1)
        self.assertEqual(counts_by_date[ten_days_month], 1)
        self.assertEqual(counts_by_date[today_month], 1)
        self.assertEqual(data["growth"][-1]["cumulative_total"], 3)

    def test_lead_growth_validates_interval_and_date_order(self):
        response = self.client.get(
            f'{self.url("lead-growth")}&interval=year'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        today = timezone.localdate().isoformat()
        response = self.client.get(
            f'{self.url("lead-growth")}'
            f"&start_date={today}&end_date=2020-01-01"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_lead_ai_insights_are_paginated_newest_week_first(self):
        today = timezone.localdate()
        this_monday = today - timedelta(days=today.weekday())
        for weeks_ago in range(1, 4):
            week_start = this_monday - timedelta(days=7 * weeks_ago)
            LeadAIInsight.objects.create(
                chatbot=self.chatbot,
                week_start=week_start,
                week_end=week_start + timedelta(days=6),
                session_count=weeks_ago,
                visitor_message_count=10 * weeks_ago,
                summary=f"Demo summary for week {weeks_ago}.",
                topics=[{"topic": "Pricing", "mentions": 3, "note": ""}],
                frequently_asked_questions=[
                    {"question": "How much?", "times_asked": 2}
                ],
                common_intents=[{"intent": "pricing", "mentions": 4}],
                areas_of_improvement=["Add pricing docs."],
                metadata={"model": "demo"},
            )

        response = self.client.get(
            f'{self.url("lead-ai-insight-list")}&page_size=2'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        self.assertEqual(len(response.data["data"]), 2)
        first = response.data["data"][0]
        self.assertEqual(first["chatbot_id"], str(self.chatbot.id))
        self.assertEqual(
            first["week_start"],
            (this_monday - timedelta(days=7)).isoformat(),
        )
        self.assertIn("summary", first)
        self.assertIn("topics", first)
        self.assertIn("frequently_asked_questions", first)
        self.assertIn("common_intents", first)
        self.assertIn("areas_of_improvement", first)

    def test_notes_can_be_created_fetched_updated_listed_and_deleted(self):
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Noted Lead"},
        )

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
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "First Lead"},
        )
        other_lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Second Lead"},
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
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Method Lead"},
        )

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
