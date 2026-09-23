from django.contrib.auth import get_user_model
from django.test import TestCase

from agent.helpers.global_tools import (
    LEAD_SCORE_SUMMARY_KEY,
    _record_lead_score,
    _request_human_escalation,
)
from chatbot.models import Chatbot
from chat.models import ChatMessage, ChatSession
from chat.utils.choices import ChatMessageSenderType
from lead_capture.models import Lead
from workspace.models import Workspace


class ConversationToolPersistenceTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(
            email="agent-tools@example.com",
            password="StrongPass123!",
        )
        workspace = Workspace.objects.create(
            name="Agent tools workspace",
            slug="agent-tools-workspace",
            owner=owner,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=workspace,
            chatbot_name="Assistant",
            slug="agent-tools-assistant",
            created_by=owner,
        )
        self.lead = Lead.objects.create(chatbot=self.chatbot)
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            lead=self.lead,
        )

    def test_record_lead_score_updates_lead_and_reason(self):
        result = _record_lead_score(
            self.chatbot.id,
            self.session.id,
            78,
            "  Has a concrete need and wants to buy this month.  ",
        )

        self.lead.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.lead.lead_score, 78)
        self.assertEqual(
            self.session.metadata[LEAD_SCORE_SUMMARY_KEY],
            "Has a concrete need and wants to buy this month.",
        )
        self.assertTrue(result["recorded"])

    def test_escalation_persists_latest_attention_fields(self):
        first = _request_human_escalation(
            self.chatbot.id,
            self.session.id,
            "Visitor explicitly requested a person.",
        )

        self.session.refresh_from_db()
        self.assertTrue(self.session.requires_attention)
        self.assertEqual(
            self.session.attention_reason,
            "Visitor explicitly requested a person.",
        )
        self.assertIsNotNone(self.session.attention_requested_at)
        self.assertEqual(
            first["escalation_reason"],
            "Visitor explicitly requested a person.",
        )
        first_timeline_message = ChatMessage.objects.get(
            chat_session=self.session,
            metadata__event_type="session.attention_requested",
            metadata__reason="Visitor explicitly requested a person.",
        )
        self.assertEqual(
            first_timeline_message.sender_type,
            ChatMessageSenderType.SYSTEM,
        )
        self.assertIsNone(first_timeline_message.sender)

        changed = _request_human_escalation(
            self.chatbot.id,
            self.session.id,
            "Availability lookup failed.",
        )
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.attention_reason,
            "Availability lookup failed.",
        )
        self.assertEqual(changed["escalation_reason"], "Availability lookup failed.")
        self.assertEqual(
            ChatMessage.objects.filter(
                chat_session=self.session,
                metadata__event_type="session.attention_requested",
            ).count(),
            2,
        )

    def test_score_is_not_recorded_without_a_captured_lead(self):
        self.session.lead = None
        self.session.save(update_fields=["lead", "updated_at"])

        result = _record_lead_score(
            self.chatbot.id,
            self.session.id,
            45,
            "Shows interest but has not provided contact details.",
        )

        self.assertFalse(result["recorded"])
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.lead_score)
