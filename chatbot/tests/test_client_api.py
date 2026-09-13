from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from chatbot.models import (
    Chatbot,
    ChatbotAllowedOrigin,
    ChatbotCapacity,
    ChatbotInvitation,
    ChatbotUser,
)
from appointment_booking.models import Appointment, AppointmentBookingConfig
from chatbot.services import create_chatbot
from chatbot.utils.choices import ChatbotPermissionTypes, ChatbotRoleTypes
from chat.models import ChatMessage, ChatSession
from chat.services.visitor_tokens import issue_conversation_token
from chat.utils.choices import (
    ChatMessageSenderType,
    ChatSessionChannel,
)
from lead_capture.models import Lead, LeadCaptureConfig
from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    PlanFeature,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription, PlanPrice, SubscriptionPlan
from workspace.models import Workspace
from workspace.services import add_workspace_user, ensure_personal_workspace

User = get_user_model()


class ChatbotClientAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
        )
        self.member = User.objects.create_user(
            email="member@example.com",
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
        ChatbotUser.objects.create(chatbot=self.chatbot, user=self.member)
        self.client.force_authenticate(self.owner)

    def test_chatbot_base_returns_subscription_backed_capabilities(self):
        capacity = ChatbotCapacity.objects.get(chatbot=self.chatbot)
        capacity.active_features = [
            PlanFeature.KNOWLEDGE_BASE,
            PlanFeature.HUMAN_HANDOFF,
            PlanFeature.APPOINTMENT_BOOKING,
            PlanFeature.LEAD_CAPTURE,
        ]
        capacity.save(update_fields=["active_features", "updated_at"])
        subscription = self.chatbot.subscriptions.get(
            status=SubscriptionStatus.ACTIVE
        )

        response = self.client.get(
            reverse("chatbot-base"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Chatbot fetched successfully.")
        self.assertEqual(
            response.data["data"],
            {
                "workspace": {
                    "name": self.workspace.name,
                    "slug": self.workspace.slug,
                },
                "chatbot_name": self.chatbot.chatbot_name,
                "slug": self.chatbot.slug,
                "language": self.chatbot.language,
                "timezone": self.chatbot.timezone,
                "features": {
                    "knowledge_base_enabled": True,
                    "human_handoff_enabled": True,
                    "appointment_booking_enabled": True,
                    "lead_captures_enabled": True,
                },
                "capacity": {
                    "ai_message_limit": capacity.ai_message_limit,
                    "current_ai_message_count": capacity.current_ai_message_count,
                    "file_size_limit_bytes": capacity.file_size_limit_bytes,
                    "current_file_size_bytes": capacity.current_file_size_bytes,
                    "knowledge_chunk_limit": capacity.knowledge_chunk_limit,
                    "current_knowledge_chunk_count": (
                        capacity.current_knowledge_chunk_count
                    ),
                    "active_features": capacity.active_features,
                },
                "current_subscription_plan": {
                    "name": subscription.get_plan_name(),
                    "is_free": subscription.is_free_plan(),
                    "billing_interval": subscription.get_billing_interval(),
                    "status": subscription.status,
                    "current_period_start": subscription.current_period_start,
                    "current_period_end": subscription.current_period_end,
                    "cancel_at_period_end": subscription.cancel_at_period_end,
                },
                "ai_enabled": self.chatbot.ai_enabled,
                "logo": self.chatbot.logo,
                "status": self.chatbot.status,
            },
        )

    def test_chatbot_base_rejects_non_member(self):
        outsider = User.objects.create_user(
            email="outsider@example.com",
            password="StrongPass123!",
        )
        self.client.force_authenticate(outsider)

        response = self.client.get(
            reverse("chatbot-base"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_public_chatbot_returns_widget_configuration_by_public_key(self):
        widget_settings = self.chatbot.widget_settings
        widget_settings.launcher_text = "Chat with us"
        widget_settings.other_settings = {"corner_radius": 12}
        widget_settings.save()
        ChatbotAllowedOrigin.objects.create(
            chatbot=self.chatbot,
            origin="https://app.example.com",
            created_by=self.owner,
        )
        self.client.force_authenticate(user=None)

        response = self.client.get(
            reverse(
                "public-chatbot",
                kwargs={"public_key": widget_settings.public_key},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["chatbot_name"], self.chatbot.chatbot_name)
        self.assertEqual(data["welcome_message"], self.chatbot.welcome_message)
        self.assertEqual(data["widget_settings"]["launcher_text"], "Chat with us")
        self.assertEqual(
            data["widget_settings"]["other_settings"],
            {"corner_radius": 12},
        )
        self.assertNotIn("id", data)
        self.assertNotIn("public_key", data["widget_settings"])
        self.assertNotIn("allowed_urls", data)
        self.assertIsNone(data["lead_config"])
        self.assertIsNone(data["appointment_config"])

    def test_public_chatbot_returns_enabled_appointment_config(self):
        config = AppointmentBookingConfig.objects.create(
            chatbot=self.chatbot,
            is_enabled=True,
            confirmation_message="Your request has been received.",
        )
        self.client.force_authenticate(user=None)

        response = self.client.get(
            reverse(
                "public-chatbot",
                kwargs={"public_key": self.chatbot.widget_settings.public_key},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"]["appointment_config"],
            {
                "collectable_fields": config.collectable_fields,
                "confirmation_message": "Your request has been received.",
            },
        )

    def test_public_chatbot_returns_enabled_lead_config(self):
        lead_config = LeadCaptureConfig.objects.create(
            chatbot=self.chatbot,
            is_enabled=True,
            require_consent=True,
            consent_message="May we save your details?",
        )
        self.client.force_authenticate(user=None)

        response = self.client.get(
            reverse(
                "public-chatbot",
                kwargs={
                    "public_key": self.chatbot.widget_settings.public_key,
                },
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"]["lead_config"],
            {
                "is_enabled": True,
                "collectable_fields": lead_config.collectable_fields,
                "auto_collect": True,
                "require_consent": True,
                "consent_message": "May we save your details?",
            },
        )

    def test_public_chatbot_hides_disabled_lead_config(self):
        LeadCaptureConfig.objects.create(
            chatbot=self.chatbot,
            is_enabled=False,
        )
        self.client.force_authenticate(user=None)

        response = self.client.get(
            reverse(
                "public-chatbot",
                kwargs={
                    "public_key": self.chatbot.widget_settings.public_key,
                },
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["data"]["lead_config"])

    def test_public_chatbot_returns_not_found_for_unknown_key(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(
            reverse("public-chatbot", kwargs={"public_key": "unknown-key"})
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_chatbot_returns_not_found_when_widget_is_disabled(self):
        widget_settings = self.chatbot.widget_settings
        widget_settings.is_enabled = False
        widget_settings.save(update_fields=["is_enabled", "updated_at"])
        self.client.force_authenticate(user=None)

        response = self.client.get(
            reverse(
                "public-chatbot",
                kwargs={"public_key": widget_settings.public_key},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_chatbot_returns_not_found_when_chatbot_is_disabled(self):
        self.chatbot.status = "disabled"
        self.chatbot.save(update_fields=["status", "updated_at"])
        self.client.force_authenticate(user=None)

        response = self.client.get(
            reverse(
                "public-chatbot",
                kwargs={
                    "public_key": self.chatbot.widget_settings.public_key,
                },
            )
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_visitor_conversation_can_be_created_and_resumed(self):
        self.client.force_authenticate(user=None)
        url = reverse(
            "visitor-conversation",
            kwargs={"public_key": self.chatbot.widget_settings.public_key},
        )

        created_response = self.client.post(
            url,
            {
                "user_metadata": {"locale": "en-US"},
                "metadata": {"page_url": "https://example.com/pricing"},
            },
            format="json",
        )

        self.assertEqual(created_response.status_code, status.HTTP_201_CREATED)
        created_data = created_response.data["data"]
        self.assertFalse(created_data["resumed"])
        self.assertTrue(created_data["conversation_token"])
        self.assertIn(
            f"/conversations/{created_data['session']['id']}/?token=",
            created_data["websocket_url"],
        )
        self.assertEqual(created_data["messages"], [])

        resumed_response = self.client.post(
            url,
            {"conversation_token": created_data["conversation_token"]},
            format="json",
        )

        self.assertEqual(resumed_response.status_code, status.HTTP_200_OK)
        resumed_data = resumed_response.data["data"]
        self.assertTrue(resumed_data["resumed"])
        self.assertEqual(
            resumed_data["session"]["id"],
            created_data["session"]["id"],
        )

    def test_visitor_conversation_rejects_invalid_token(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse(
                "visitor-conversation",
                kwargs={"public_key": self.chatbot.widget_settings.public_key},
            ),
            {"conversation_token": "not-a-valid-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("conversation_token", response.data["errors"])

    def test_visitor_conversation_creates_lead_from_lead_data(self):
        LeadCaptureConfig.objects.create(
            chatbot=self.chatbot,
            is_enabled=True,
        )
        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse(
                "visitor-conversation",
                kwargs={
                    "public_key": self.chatbot.widget_settings.public_key,
                },
            ),
            {
                "lead_data": {
                    "name": "Ada Lovelace",
                    "email": "ADA@EXAMPLE.COM",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        session = ChatSession.objects.get(
            pk=response.data["data"]["session"]["id"]
        )
        self.assertIsNotNone(session.lead_id)
        self.assertEqual(session.lead.source, "web_widget")
        self.assertEqual(
            session.lead.collected_fields,
            {"name": "Ada Lovelace", "email": "ada@example.com"},
        )

    def test_visitor_conversation_accepts_existing_lead(self):
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Existing visitor"},
        )
        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse(
                "visitor-conversation",
                kwargs={
                    "public_key": self.chatbot.widget_settings.public_key,
                },
            ),
            {"lead_id": str(lead.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        session = ChatSession.objects.get(
            pk=response.data["data"]["session"]["id"]
        )
        self.assertEqual(session.lead_id, lead.id)

    def test_visitor_conversation_rejects_invalid_lead_data(self):
        LeadCaptureConfig.objects.create(
            chatbot=self.chatbot,
            is_enabled=True,
        )
        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse(
                "visitor-conversation",
                kwargs={
                    "public_key": self.chatbot.widget_settings.public_key,
                },
            ),
            {"lead_data": {"name": "Missing required email"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("lead_data", response.data["errors"])
        self.assertFalse(Lead.objects.filter(chatbot=self.chatbot).exists())

    def test_public_visitor_details_and_sessions_include_linked_lead(self):
        LeadCaptureConfig.objects.create(
            chatbot=self.chatbot,
            is_enabled=True,
        )
        self.client.force_authenticate(user=None)
        conversation_url = reverse(
            "visitor-conversation",
            kwargs={"public_key": self.chatbot.widget_settings.public_key},
        )
        first_response = self.client.post(
            conversation_url,
            {
                "user_metadata": {"locale": "en-US"},
                "lead_data": {
                    "name": "Ada Lovelace",
                    "email": "ada@example.com",
                },
            },
            format="json",
        )
        first_session = ChatSession.objects.get(
            pk=first_response.data["data"]["session"]["id"]
        )
        second_response = self.client.post(
            conversation_url,
            {"lead_id": str(first_session.lead_id)},
            format="json",
        )
        third_response = self.client.post(
            conversation_url,
            {"lead_id": str(first_session.lead_id)},
            format="json",
        )
        second_session = ChatSession.objects.get(
            pk=second_response.data["data"]["session"]["id"]
        )
        third_session = ChatSession.objects.get(
            pk=third_response.data["data"]["session"]["id"]
        )
        ChatMessage.objects.create(
            chat_session=first_session,
            sender_type=ChatMessageSenderType.AI,
            content="How can I help?",
        )
        self.member.name = "Support Agent"
        self.member.save(update_fields=["name"])
        agent = ChatbotUser.objects.get(
            chatbot=self.chatbot,
            user=self.member,
        )
        ChatMessage.objects.create(
            chat_session=second_session,
            sender_type=ChatMessageSenderType.AGENT,
            sender=agent,
            content="I can take it from here.",
        )
        ChatMessage.objects.create(
            chat_session=third_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="I need some help.",
        )
        visitor_id = first_session.visitor_id
        url_kwargs = {
            "public_key": self.chatbot.widget_settings.public_key,
            "visitor_id": visitor_id,
        }

        detail_response = self.client.get(
            reverse("public-visitor-detail", kwargs=url_kwargs)
        )
        sessions_response = self.client.get(
            reverse("public-visitor-sessions", kwargs=url_kwargs)
        )

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            detail_response.data["data"],
            {
                "visitor_id": visitor_id,
                "lead_id": str(first_session.lead_id),
                "lead_data": {
                    "name": "Ada Lovelace",
                    "email": "ada@example.com",
                },
                "user_metadata": {"locale": "en-US"},
            },
        )
        self.assertEqual(sessions_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(sessions_response.data["data"]), 3)
        self.assertEqual(
            {item["id"] for item in sessions_response.data["data"]},
            {
                first_response.data["data"]["session"]["id"],
                second_response.data["data"]["session"]["id"],
                third_response.data["data"]["session"]["id"],
            },
        )
        sessions_by_id = {
            item["id"]: item for item in sessions_response.data["data"]
        }
        self.assertEqual(
            sessions_by_id[str(first_session.id)]["last_message"],
            {
                "content": "How can I help?",
                "sender": self.chatbot.chatbot_name,
            },
        )
        self.assertEqual(
            sessions_by_id[str(second_session.id)]["last_message"],
            {
                "content": "I can take it from here.",
                "sender": "Agent",
                "agent_name": self.member.name,
            },
        )
        self.assertEqual(
            sessions_by_id[str(third_session.id)]["last_message"],
            {
                "content": "I need some help.",
                "sender": "You",
            },
        )

    def test_visitor_conversation_enforces_configured_origin(self):
        ChatbotAllowedOrigin.objects.create(
            chatbot=self.chatbot,
            origin="https://allowed.example.com",
            created_by=self.owner,
        )
        self.client.force_authenticate(user=None)
        url = reverse(
            "visitor-conversation",
            kwargs={"public_key": self.chatbot.widget_settings.public_key},
        )

        rejected_response = self.client.post(url, {}, format="json")
        accepted_response = self.client.post(
            url,
            {},
            format="json",
            HTTP_ORIGIN="https://allowed.example.com",
        )

        self.assertEqual(rejected_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(accepted_response.status_code, status.HTTP_201_CREATED)


    def test_visitor_message_requires_conversation_bearer_token(self):
        self.client.force_authenticate(user=None)
        public_key = self.chatbot.widget_settings.public_key
        bootstrap_response = self.client.post(
            reverse(
                "visitor-conversation",
                kwargs={"public_key": public_key},
            ),
            {},
            format="json",
        )
        session_id = bootstrap_response.data["data"]["session"]["id"]

        response = self.client.post(
            reverse(
                "visitor-message-create",
                kwargs={"public_key": public_key, "session_id": session_id},
            ),
            {"content": "Hello"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("chatbot.api.v1.client.views.record_ai_usage")
    @patch("chatbot.api.v1.client.views.AgentClient")
    @patch("appointment_booking.services.available_slots")
    def test_visitor_can_book_available_slot_and_notify_agent(
        self,
        available_slots,
        agent_client,
        record_ai_usage,
    ):
        AppointmentBookingConfig.objects.create(
            chatbot=self.chatbot,
            is_enabled=True,
        )
        session = ChatSession.objects.create(
            chatbot=self.chatbot,
            visitor_id="booking-visitor",
            channel=ChatSessionChannel.WEB_WIDGET,
        )
        token = issue_conversation_token(session)
        starts_at = timezone.now() + timedelta(days=2)
        ends_at = starts_at + timedelta(minutes=30)
        available_slots.return_value = [{
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
        }]
        agent_client.return_value.confirm_booking_sync.return_value = {
            "result": {"content": "Your appointment request is awaiting approval."},
            "token": {"total_tokens": 20},
            "cost": 0.0001,
        }
        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse(
                "visitor-appointment-create",
                kwargs={
                    "public_key": self.chatbot.widget_settings.public_key,
                    "session_id": session.id,
                },
            ),
            {
                "starts_at": starts_at.isoformat(),
                "collected_fields": {
                    "name": "Ada Lovelace",
                    "email": "ada@example.com",
                },
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        appointment = Appointment.objects.get(
            pk=response.data["data"]["appointment"]["id"]
        )
        self.assertEqual(
            appointment.metadata,
            {
                "chat_session_id": str(session.id),
                "source": "web_widget",
            },
        )
        self.assertEqual(appointment.starts_at, starts_at)
        self.assertEqual(appointment.ends_at, ends_at)
        agent_client.return_value.confirm_booking_sync.assert_called_once_with(
            appointment_id=str(appointment.id),
            user_id="booking-visitor",
        )
        self.assertTrue(response.data["data"]["agent_acknowledged"])
        record_ai_usage.assert_called_once()

    def test_chatbot_detail_uses_chatbot_query_parameter(self):
        response = self.client.get(
            reverse("chatbot-detail"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["slug"], self.chatbot.slug)

    def test_chatbot_widget_details_returns_widget_and_basic_chatbot_details(self):
        widget_settings = self.chatbot.widget_settings
        widget_settings.launcher_text = "Chat with support"
        widget_settings.header_title = "Support"
        widget_settings.other_settings = {"corner_radius": 12}
        widget_settings.save()
        enabled_origin = ChatbotAllowedOrigin.objects.create(
            chatbot=self.chatbot,
            origin="https://app.example.com",
            created_by=self.owner,
        )
        disabled_origin = ChatbotAllowedOrigin.objects.create(
            chatbot=self.chatbot,
            origin="https://disabled.example.com",
            is_active=False,
            created_by=self.owner,
        )

        response = self.client.get(
            reverse("chatbot-widget-details"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["id"], str(self.chatbot.id))
        self.assertEqual(data["chatbot_name"], self.chatbot.chatbot_name)
        self.assertEqual(data["business_name"], self.chatbot.business_name)
        self.assertEqual(data["slug"], self.chatbot.slug)
        self.assertEqual(data["logo"], self.chatbot.logo)
        self.assertEqual(data["welcome_message"], self.chatbot.welcome_message)
        self.assertEqual(data["status"], self.chatbot.status)
        self.assertEqual(
            data["allowed_urls"],
            [
                {
                    "id": str(enabled_origin.id),
                    "url": "https://app.example.com",
                    "is_active": True,
                },
                {
                    "id": str(disabled_origin.id),
                    "url": "https://disabled.example.com",
                    "is_active": False,
                },
            ],
        )

        widget_data = data["widget_settings"]
        self.assertEqual(widget_data["id"], str(widget_settings.id))
        self.assertEqual(widget_data["public_key"], widget_settings.public_key)
        self.assertTrue(widget_data["is_enabled"])
        self.assertEqual(widget_data["launcher_text"], "Chat with support")
        self.assertEqual(widget_data["header_title"], "Support")
        self.assertEqual(widget_data["other_settings"], {"corner_radius": 12})

    def test_chatbot_widget_details_requires_chatbot_membership(self):
        outsider = User.objects.create_user(
            email="widget-outsider@example.com",
            password="StrongPass123!",
        )
        self.client.force_authenticate(outsider)

        response = self.client.get(
            reverse("chatbot-widget-details"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_chatbot_widget_update_changes_settings_and_upserts_allowed_urls(self):
        existing_origin = ChatbotAllowedOrigin.objects.create(
            chatbot=self.chatbot,
            origin="https://old.example.com",
            created_by=self.owner,
        )
        omitted_origin = ChatbotAllowedOrigin.objects.create(
            chatbot=self.chatbot,
            origin="https://unchanged.example.com",
            created_by=self.owner,
        )

        response = self.client.patch(
            reverse("chatbot-widget-update"),
            {
                "widget_settings": {
                    "primary_color": "#112233",
                    "launcher_text": "Ask us anything",
                    "theme": "dark",
                },
                "allowed_urls": [
                    {
                        "id": str(existing_origin.id),
                        "url": "https://new.example.com/",
                        "is_active": False,
                    },
                    {
                        "url": "https://fresh.example.com/",
                        "is_active": True,
                    },
                ],
            },
            format="json",
            QUERY_STRING=urlencode({"chatbot": self.chatbot.slug}),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        widget_data = response.data["data"]["widget_settings"]
        self.assertEqual(widget_data["primary_color"], "#112233")
        self.assertEqual(widget_data["launcher_text"], "Ask us anything")
        self.assertEqual(widget_data["theme"], "dark")

        existing_origin.refresh_from_db()
        self.assertEqual(existing_origin.origin, "https://new.example.com")
        self.assertFalse(existing_origin.is_active)
        self.assertEqual(existing_origin.updated_by, self.owner)
        self.assertTrue(
            ChatbotAllowedOrigin.objects.filter(
                chatbot=self.chatbot,
                origin="https://fresh.example.com",
                is_active=True,
                created_by=self.owner,
                updated_by=self.owner,
            ).exists()
        )
        omitted_origin.refresh_from_db()
        self.assertTrue(omitted_origin.is_active)

        allowed_urls = response.data["data"]["allowed_urls"]
        self.assertEqual(
            {item["url"]: item["is_active"] for item in allowed_urls},
            {
                "https://fresh.example.com": True,
                "https://new.example.com": False,
                "https://unchanged.example.com": True,
            },
        )

    def test_chatbot_widget_update_requires_setup_permission(self):
        self.client.force_authenticate(self.member)

        response = self.client.patch(
            reverse("chatbot-widget-update"),
            {"widget_settings": {"theme": "dark"}},
            format="json",
            QUERY_STRING=urlencode({"chatbot": self.chatbot.slug}),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_chatbot_widget_update_removes_allowed_url_by_id(self):
        removed_origin = ChatbotAllowedOrigin.objects.create(
            chatbot=self.chatbot,
            origin="https://remove.example.com",
            created_by=self.owner,
        )
        retained_origin = ChatbotAllowedOrigin.objects.create(
            chatbot=self.chatbot,
            origin="https://retain.example.com",
            created_by=self.owner,
        )

        response = self.client.patch(
            reverse("chatbot-widget-update"),
            {"removed_allowed_url_id": str(removed_origin.id)},
            format="json",
            QUERY_STRING=urlencode({"chatbot": self.chatbot.slug}),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            ChatbotAllowedOrigin.objects.filter(id=removed_origin.id).exists()
        )
        self.assertTrue(
            ChatbotAllowedOrigin.objects.filter(id=retained_origin.id).exists()
        )
        self.assertEqual(
            response.data["data"]["allowed_urls"],
            [
                {
                    "id": str(retained_origin.id),
                    "url": retained_origin.origin,
                    "is_active": True,
                }
            ],
        )

    def test_chatbot_widget_update_rejects_foreign_allowed_url_removal(self):
        other_chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Other Chatbot",
            created_by=self.owner,
        )
        foreign_origin = ChatbotAllowedOrigin.objects.create(
            chatbot=other_chatbot,
            origin="https://other.example.com",
            created_by=self.owner,
        )

        response = self.client.patch(
            reverse("chatbot-widget-update"),
            {"removed_allowed_url_id": str(foreign_origin.id)},
            format="json",
            QUERY_STRING=urlencode({"chatbot": self.chatbot.slug}),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("removed_allowed_url_id", response.data["errors"])
        self.assertTrue(
            ChatbotAllowedOrigin.objects.filter(id=foreign_origin.id).exists()
        )

    def test_chatbot_create_accepts_core_settings_and_creates_widget_settings(self):
        response = self.client.post(
            reverse("chatbot-create"),
            {
                "workspace": self.workspace.slug,
                "chatbot_name": "Configured Bot",
                "business_name": "Argon Support",
                "welcome_message": "Welcome!",
                "fallback_message": "Please try again.",
                "instructions": "Be concise.",
                "escalation_rule": "Escalate refund requests.",
                "never_answer": "Do not provide legal advice.",
                "language": "bn",
                "timezone": "Asia/Dhaka",
                "ai_enabled": False,
                "knowledge_base_enabled": False,
                "human_handoff_enabled": True,
                "other_settings": {"response_tone": "friendly"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        chatbot = Chatbot.objects.get(chatbot_name="Configured Bot")
        self.assertEqual(chatbot.business_name, "Argon Support")
        self.assertEqual(
            response.data["data"]["chatbot_name"],
            "Configured Bot",
        )
        self.assertEqual(
            response.data["data"]["business_name"],
            "Argon Support",
        )
        self.assertEqual(chatbot.welcome_message, "Welcome!")
        self.assertEqual(chatbot.fallback_message, "Please try again.")
        self.assertEqual(chatbot.instructions, "Be concise.")
        self.assertEqual(chatbot.escalation_rule, "Escalate refund requests.")
        self.assertEqual(chatbot.never_answer, "Do not provide legal advice.")
        self.assertEqual(
            response.data["data"]["escalation_rule"],
            "Escalate refund requests.",
        )
        self.assertEqual(
            response.data["data"]["never_answer"],
            "Do not provide legal advice.",
        )
        self.assertEqual(chatbot.language, "bn")
        self.assertEqual(chatbot.timezone, "Asia/Dhaka")
        self.assertFalse(chatbot.ai_enabled)
        self.assertFalse(chatbot.knowledge_base_enabled)
        self.assertEqual(chatbot.other_settings["response_tone"], "friendly")
        self.assertTrue(chatbot.widget_settings.public_key)

    def test_chatbot_create_uses_default_conversation_messages(self):
        response = self.client.post(
            reverse("chatbot-create"),
            {
                "workspace": self.workspace.slug,
                "chatbot_name": "Default Message Bot",
                "business_name": "Argon",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data["data"]
        self.assertEqual(data["chatbot_name"], "Default Message Bot")
        self.assertEqual(data["business_name"], "Argon")
        self.assertEqual(
            data["welcome_message"],
            (
                "Hey, I am Default Message Bot, I am here to answer anything "
                "you want to know about Argon."
            ),
        )
        self.assertEqual(
            data["fallback_message"],
            (
                "Sorry, I couldn't find anything to my knowledge to answer "
                "this question, should I connect with you one of our human "
                "assistant?"
            ),
        )
        self.assertEqual(
            data["escalation_rule"],
            (
                "Hand off to human agent, when you don't find any answer, "
                "asking about payment or collaboration."
            ),
        )
        self.assertEqual(
            data["never_answer"],
            "Never answer about payment, outside scope and all.",
        )

    def test_chatbot_list_returns_page_metadata(self):
        create_chatbot(
            workspace=self.workspace,
            chatbot_name="Sales Bot",
            created_by=self.owner,
        )

        response = self.client.get(
            reverse("chatbot-list"),
            {"page": 1, "page_size": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page"], 1)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(response.data["meta"]["num_pages"], 2)
        self.assertIsNotNone(response.data["meta"]["next"])
        self.assertIsNone(response.data["meta"]["previous"])

    def test_chatbot_list_returns_compact_details_creator_and_members(self):
        self.owner.name = "Chatbot Owner"
        self.owner.save(update_fields=["name"])
        self.owner.profile.avatar_url = "https://example.com/owner.png"
        self.owner.profile.save(update_fields=["avatar_url"])
        self.member.name = "Chatbot Member"
        self.member.save(update_fields=["name"])
        self.member.profile.avatar_url = "https://example.com/member.png"
        self.member.profile.save(update_fields=["avatar_url"])
        plan = SubscriptionPlan.objects.create(name="Growth")
        plan_price = PlanPrice.objects.create(
            plan=plan,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("19.00"),
        )
        ChatbotSubscription.objects.create(
            chatbot=self.chatbot,
            plan_price=plan_price,
            selected_by=self.owner,
            provider=PaymentProvider.STRIPE,
            renewal_mode=RenewalMode.PROVIDER_MANAGED,
            status=SubscriptionStatus.ACTIVE,
        )

        response = self.client.get(
            reverse("chatbot-list"),
            {"page_size": 10},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        data = response.data["data"][0]
        self.assertEqual(
            set(data),
            {
                "slug",
                "chatbot_name",
                "business_name",
                "description",
                "ai_enabled",
                "logo",
                "status",
                "current_user_role",
                "subscription_plan_name",
                "created_by",
                "members",
            },
        )
        self.assertEqual(data["slug"], self.chatbot.slug)
        self.assertEqual(data["chatbot_name"], self.chatbot.chatbot_name)
        self.assertEqual(data["business_name"], self.chatbot.business_name)
        self.assertEqual(data["description"], self.chatbot.description)
        self.assertEqual(data["ai_enabled"], self.chatbot.ai_enabled)
        self.assertEqual(data["logo"], self.chatbot.logo)
        self.assertEqual(data["status"], self.chatbot.status)
        self.assertEqual(data["current_user_role"], ChatbotRoleTypes.ADMIN)
        self.assertEqual(data["subscription_plan_name"], plan.name)
        self.assertEqual(
            data["created_by"],
            {
                "name": self.owner.name,
                "email": self.owner.email,
                "avatar": self.owner.profile.avatar_url,
            },
        )
        self.assertEqual(
            data["members"],
            [
                {
                    "name": self.member.name,
                    "avatar": self.member.profile.avatar_url,
                },
                {
                    "name": self.owner.name,
                    "avatar": self.owner.profile.avatar_url,
                },
            ],
        )

    def test_workspace_member_lists_and_opens_only_assigned_chatbots(self):
        workspace_chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Workspace Bot",
            created_by=self.owner,
        )
        self.client.force_authenticate(self.member)

        response = self.client.get(
            reverse("chatbot-list"),
            {"page_size": 10},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["slug"] for item in response.data["data"]},
            {self.chatbot.slug},
        )
        self.assertEqual(
            response.data["data"][0]["workspace"],
            {
                "id": str(self.workspace.id),
                "name": self.workspace.name,
                "slug": self.workspace.slug,
            },
        )

        response = self.client.get(
            reverse("chatbot-detail"),
            {"chatbot": workspace_chatbot.slug},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        response = self.client.patch(
            (
                f'{reverse("chatbot-update")}?'
                f'{urlencode({"chatbot": workspace_chatbot.slug})}'
            ),
            {"description": "Not allowed without chatbot permission"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_chatbot_only_member_lists_only_assigned_chatbots(self):
        chatbot_only_member = User.objects.create_user(
            email="chatbot-only@example.com",
            password="StrongPass123!",
        )
        ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=chatbot_only_member,
        )
        other_chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Other Bot",
            created_by=self.owner,
        )
        self.client.force_authenticate(chatbot_only_member)

        response = self.client.get(
            reverse("chatbot-list"),
            {"page_size": 10},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)
        self.assertEqual(response.data["data"][0]["slug"], self.chatbot.slug)

        response = self.client.get(
            reverse("chatbot-detail"),
            {"chatbot": other_chatbot.slug},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_can_access_multiple_chatbots_across_workspaces_and_filter_them(self):
        user = User.objects.create_user(
            email="multi-chatbot@example.com",
            password="StrongPass123!",
        )
        same_workspace_chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Same Workspace Bot",
            created_by=self.owner,
        )
        other_owner = User.objects.create_user(
            email="other-owner@example.com",
            password="StrongPass123!",
        )
        other_workspace = ensure_personal_workspace(other_owner)
        other_workspace_chatbot = create_chatbot(
            workspace=other_workspace,
            chatbot_name="Other Workspace Bot",
            created_by=other_owner,
        )
        ChatbotUser.objects.bulk_create(
            [
                ChatbotUser(chatbot=self.chatbot, user=user),
                ChatbotUser(chatbot=same_workspace_chatbot, user=user),
                ChatbotUser(chatbot=other_workspace_chatbot, user=user),
            ]
        )
        self.client.force_authenticate(user)

        response = self.client.get(
            reverse("chatbot-list"),
            {"page_size": 10},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        self.assertEqual(
            {item["workspace"]["slug"] for item in response.data["data"]},
            {self.workspace.slug, other_workspace.slug},
        )

        response = self.client.get(
            reverse("chatbot-list"),
            {"workspace": self.workspace.slug, "page_size": 10},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["slug"] for item in response.data["data"]},
            {self.chatbot.slug, same_workspace_chatbot.slug},
        )

    def test_chatbot_member_list_returns_page_metadata(self):
        response = self.client.get(
            reverse("chatbot-members"),
            {
                "chatbot": self.chatbot.slug,
                "page": 1,
                "page_size": 1,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page"], 1)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(response.data["meta"]["num_pages"], 2)

    def test_chatbot_member_list_returns_profile_and_activity_data(self):
        avatar = "https://example.com/member-avatar.png"
        self.member.profile.avatar_url = avatar
        self.member.profile.save(update_fields=["avatar_url"])
        self.member.last_active = timezone.now()
        self.member.last_login = timezone.now()
        self.member.save(update_fields=["last_active", "last_login"])

        response = self.client.get(
            reverse("chatbot-members"),
            {"chatbot": self.chatbot.slug, "page_size": 10},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        member_data = next(
            item
            for item in response.data["data"]
            if item["user"]["email"] == self.member.email
        )
        self.assertEqual(
            member_data["user"],
            {
                "email": self.member.email,
                "name": self.member.name,
                "avatar": avatar,
            },
        )
        self.assertFalse(member_data["all_permissions"])
        self.assertNotIn("effective_permissions", member_data)
        self.assertIsNotNone(member_data["last_active"])
        self.assertIsNotNone(member_data["last_login"])
        self.assertIsNotNone(member_data["invited_at"])

        admin_data = next(
            item
            for item in response.data["data"]
            if item["user"]["email"] == self.owner.email
        )
        self.assertTrue(admin_data["all_permissions"])

    def test_chatbot_member_detail_uses_chatbot_and_email_query_parameters(self):
        response = self.client.get(
            reverse("chatbot-member-details"),
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"]["user"]["email"],
            self.member.email,
        )

    @patch("chatbot.api.v1.client.serializers.issue_chatbot_invitation")
    def test_invite_accepts_email_and_permissions_payload(self, issue_invitation):
        permissions = [
            ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT,
            ChatbotPermissionTypes.SETUP_CONFIGURATION,
        ]
        invitation = ChatbotInvitation.objects.create(
            chatbot=self.chatbot,
            email=self.member.email,
            token_hash="test-token-hash",
            expires_at=timezone.now() + timedelta(hours=1),
            permissions=permissions,
            created_by=self.owner,
        )
        issue_invitation.return_value = invitation
        query = urlencode({"chatbot": self.chatbot.slug})

        response = self.client.post(
            f'{reverse("invite-chatbot-member")}?{query}',
            {
                "email": self.member.email,
                "permissions": permissions,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        issue_invitation.assert_called_once_with(
            chatbot=self.chatbot,
            email=self.member.email,
            permissions=permissions,
            invited_by=self.owner,
        )

    def test_invitation_is_account_independent_and_applies_permissions(self):
        invitee_email = "invitee@example.com"
        permissions = [
            ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT,
            ChatbotPermissionTypes.SETUP_CONFIGURATION,
        ]
        query = urlencode({"chatbot": self.chatbot.slug})
        with patch(
            "chatbot.services.invitations._deliver_chatbot_invitation"
        ) as deliver:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f'{reverse("invite-chatbot-member")}?{query}',
                    {
                        "email": invitee_email,
                        "permissions": permissions,
                    },
                    format="json",
                )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["permissions"], permissions)
        invited_at = response.data["data"]["invited_at"]
        self.assertFalse(User.objects.filter(email=invitee_email).exists())
        token = deliver.call_args.kwargs["token"]

        response = self.client.get(
            reverse("chatbot-members"),
            {"chatbot": self.chatbot.slug, "page_size": 10},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        pending_members = [
            item
            for item in response.data["data"]
            if item["user"]["email"] == invitee_email
        ]
        self.assertEqual(len(pending_members), 1)
        pending_member = pending_members[0]
        self.assertEqual(pending_member["role"], ChatbotRoleTypes.MEMBER)
        self.assertEqual(pending_member["permissions"], permissions)
        self.assertFalse(pending_member["all_permissions"])
        self.assertFalse(pending_member["is_active"])
        self.assertEqual(pending_member["invited_at"], invited_at)

        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("register"),
            {
                "email": invitee_email,
                "name": "Invited Chatbot User",
                "password": "ExistingPassword123!",
                "confirm_password": "ExistingPassword123!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invitee = User.objects.get(email=invitee_email)
        self.assertFalse(Workspace.objects.filter(owner=invitee).exists())

        self.client.force_authenticate(invitee)
        response = self.client.post(
            reverse("accept-chatbot-invitation"),
            {"token": token},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("access_token", response.data["data"])
        self.assertNotIn("refresh_token", response.data["data"])
        membership = ChatbotUser.objects.get(
            chatbot=self.chatbot,
            user=invitee,
        )
        self.assertEqual(membership.permissions, permissions)
        invitee.refresh_from_db()
        self.assertEqual(invitee.name, "Invited Chatbot User")
        self.assertTrue(invitee.check_password("ExistingPassword123!"))
        response = self.client.get(
            reverse("chatbot-detail"),
            {"chatbot": self.chatbot.slug},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.patch(
            (
                f'{reverse("chatbot-update")}?'
                f'{urlencode({"chatbot": self.chatbot.slug})}'
            ),
            {"description": "Updated by chatbot-only member"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.get(reverse("chatbot-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)
        response = self.client.get(
            reverse("chatbot-members"),
            {"chatbot": self.chatbot.slug, "page_size": 10},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        accepted_members = [
            item
            for item in response.data["data"]
            if item["user"]["email"] == invitee_email
        ]
        self.assertEqual(len(accepted_members), 1)
        self.assertTrue(accepted_members[0]["is_active"])
        self.assertEqual(accepted_members[0]["invited_at"], invited_at)

    def test_invitation_acceptance_rejects_a_different_authenticated_user(self):
        invitee_email = "different-account@example.com"
        query = urlencode({"chatbot": self.chatbot.slug})
        with patch(
            "chatbot.services.invitations._deliver_chatbot_invitation"
        ) as deliver:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f'{reverse("invite-chatbot-member")}?{query}',
                    {"email": invitee_email, "permissions": []},
                    format="json",
                )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        token = deliver.call_args.kwargs["token"]

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("accept-chatbot-invitation"),
            {"token": token},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("token", response.data["errors"])
        invitation = ChatbotInvitation.objects.get(
            chatbot=self.chatbot,
            email=invitee_email,
        )
        self.assertIsNone(invitation.accepted_at)
        self.assertFalse(User.objects.filter(email=invitee_email).exists())

    def test_jwt_activity_updates_last_active(self):
        self.client.force_authenticate(user=None)
        access_token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        response = self.client.get(reverse("chatbot-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.owner.refresh_from_db()
        self.assertIsNotNone(self.owner.last_active)

    def test_remove_member_deletes_only_membership_without_changing_account(self):
        query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            }
        )
        response = self.client.delete(
            f'{reverse("remove-chatbot-member")}?{query}',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            ChatbotUser.objects.filter(
                chatbot=self.chatbot,
                user=self.member,
            ).exists()
        )
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())

    def test_member_detail_requires_both_query_parameters(self):
        response = self.client.get(
            reverse("chatbot-member-details"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("member_email", response.data)

    def test_admin_can_manage_only_permissions_available_to_the_chatbot(self):
        query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            }
        )
        url = f'{reverse("chatbot-member-permissions")}?{query}'

        response = self.client.patch(
            url,
            {
                "permissions": [
                    ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT,
                    ChatbotPermissionTypes.LEAD_MANAGEMENT,
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        permissions = [
            ChatbotPermissionTypes.SETUP_CONFIGURATION,
        ]
        response = self.client.patch(
            url,
            {"permissions": permissions},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = ChatbotUser.objects.get(
            chatbot=self.chatbot,
            user=self.member,
        )
        self.assertEqual(membership.permissions, permissions)
        owner_membership = ChatbotUser.objects.get(
            chatbot=self.chatbot,
            user=self.owner,
        )
        self.assertTrue(owner_membership.all_permissions)
        self.assertFalse(
            owner_membership.has_permission(ChatbotPermissionTypes.LEAD_MANAGEMENT)
        )
        self.assertEqual(
            response.data["data"]["member"]["effective_permissions"],
            permissions,
        )
        self.assertEqual(
            {
                item["code"]
                for item in response.data["data"]["available_permissions"]
            },
            {
                ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT,
                ChatbotPermissionTypes.SETUP_CONFIGURATION,
            },
        )

    def test_setup_permission_is_enforced_for_chatbot_updates(self):
        update_url = (
            f'{reverse("chatbot-update")}?'
            f'{urlencode({"chatbot": self.chatbot.slug})}'
        )
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            update_url,
            {"description": "Member update"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        permission_query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            }
        )
        response = self.client.patch(
            (
                f'{reverse("chatbot-member-permissions")}?'
                f"{permission_query}"
            ),
            {
                "permissions": [
                    ChatbotPermissionTypes.SETUP_CONFIGURATION,
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.member)
        response = self.client.patch(
            update_url,
            {"description": "Member update"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.chatbot.refresh_from_db()
        self.assertEqual(self.chatbot.description, "Member update")

    def test_regular_member_cannot_manage_permissions(self):
        query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.owner.email,
            }
        )
        self.client.force_authenticate(self.member)

        response = self.client.patch(
            f'{reverse("chatbot-member-permissions")}?{query}',
            {"permissions": []},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
