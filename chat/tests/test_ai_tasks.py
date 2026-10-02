from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from analytics.choices import AIUsageType
from analytics.models import AIUsage
from analytics.services.ai_usage import record_ai_usage
from agent.client import AgentClient
from chatbot.models import Chatbot
from chat.models import ChatMessage, ChatSession
from chat.tasks import generate_ai_reply_task
from chat.utils.choices import ChatMessageSenderType
from knowledge.models import KnowledgeBase
from knowledge.utils.choices import KnowledgeSourceTypes
from workspace.models import Workspace


User = get_user_model()


@override_settings(GEMINI_CHAT_MODEL="gemini-2.5-flash")
class GenerateAIReplyTaskTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(
            email="ai-task@example.com",
            password="StrongPass123!",
        )
        workspace = Workspace.objects.create(
            name="AI task workspace",
            slug="ai-task-workspace",
            owner=owner,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=workspace,
            chatbot_name="Assistant",
            slug="ai-task-assistant",
            created_by=owner,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            visitor_id="visitor-1",
        )
        self.visitor_message = ChatMessage.objects.create(
            chat_session=self.session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Can I book tomorrow?",
        )

    @patch("chat.tasks.AgentClient")
    def test_task_returns_only_public_agent_reply(self, agent_client):
        public_reply = {
            "content": "Tomorrow is available.",
            "metadata": {
                "appointments": {
                    "date": "2026-09-11",
                    "timezone": "UTC+6.00",
                    "available_slots": [
                        {"start_time": "9.00 AM", "end_time": "9.30 AM"},
                    ],
                },
            },
        }
        agent_client.return_value.generate_reply_sync.return_value = public_reply

        result = generate_ai_reply_task.apply(
            args=[str(self.visitor_message.id)],
            throw=True,
        )

        self.assertEqual(result.result, public_reply)
        agent_client.assert_called_once_with(self.chatbot, self.session)
        agent_client.return_value.generate_reply_sync.assert_called_once_with(
            visitor_message=self.visitor_message,
            user_id="visitor-1",
        )

    @patch("chat.tasks.AgentClient")
    def test_async_reply_task_ignores_test_sessions(self, agent_client):
        test_session = ChatSession.objects.create(
            chatbot=self.chatbot,
            is_test=True,
        )
        test_message = ChatMessage.objects.create(
            chat_session=test_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Test prompt",
        )

        result = generate_ai_reply_task.apply(
            args=[str(test_message.id)],
            throw=True,
        )

        self.assertIsNone(result.result)
        self.assertEqual(test_session.messages.count(), 1)
        agent_client.assert_not_called()

    @patch("chat.tasks.AgentClient")
    def test_task_does_not_repeat_agent_side_effects(self, agent_client):
        public_reply = {
            "content": "I have requested human assistance.",
            "metadata": {
                "escalation": {
                    "reason": "Visitor explicitly requested a human.",
                },
            },
        }
        agent_client.return_value.generate_reply_sync.return_value = public_reply

        result = generate_ai_reply_task.apply(
            args=[str(self.visitor_message.id)],
            throw=True,
        )
        self.assertEqual(result.result, public_reply)

    @patch("chat.tasks.AgentClient")
    def test_appointment_trigger_uses_booking_reply_lifecycle(self, agent_client):
        public_reply = {
            "content": "Your appointment request is awaiting approval.",
            "metadata": {},
        }
        agent_client.return_value.generate_booking_reply_sync.return_value = (
            public_reply
        )

        result = generate_ai_reply_task.apply(
            kwargs={
                "chat_session_id": str(self.session.id),
                "appointment_id": "appointment-1",
            },
            throw=True,
        )

        self.assertEqual(result.result, public_reply)
        agent_client.return_value.generate_booking_reply_sync.assert_called_once_with(
            appointment_id="appointment-1",
            user_id="visitor-1",
        )

    @patch("chat.tasks.AgentClient")
    def test_disabled_session_does_not_call_agent(self, agent_client):
        self.session.ai_enabled = False
        self.session.save(update_fields=["ai_enabled", "updated_at"])

        result = generate_ai_reply_task.apply(
            args=[str(self.visitor_message.id)],
            throw=True,
        )

        self.assertIsNone(result.result)
        agent_client.assert_not_called()
        self.assertFalse(
            ChatMessage.objects.filter(
                chat_session=self.session,
                sender_type=ChatMessageSenderType.AI,
            ).exists()
        )
        self.assertFalse(AIUsage.objects.exists())

    def test_content_generation_usage_does_not_require_chat_relations(self):
        usage = record_ai_usage(
            chatbot=self.chatbot,
            usage_type=AIUsageType.CONTENT_GENERATION,
            cost="0.0002",
            token_usage={
                "input_tokens": 40,
                "output_tokens": 10,
                "total_tokens": 50,
            },
        )

        self.assertIsNone(usage.chat_session)
        self.assertIsNone(usage.chat_message)
        self.assertEqual(usage.chatbot_id_snapshot, self.chatbot.id)
        self.assertIsNone(usage.chat_session_id_snapshot)

    def test_agent_client_persists_public_metadata_and_usage(self):
        source = KnowledgeBase.objects.create(
            chatbot=self.chatbot,
            source_type=KnowledgeSourceTypes.WEBSITE,
            title="Booking FAQ",
            url="https://example.com/booking",
        )
        response = {
            "result": {
                "content": "Tomorrow is available.",
                "source_ids": [str(source.id)],
                "agreed_date": "2026-09-11",
                "lead_score": {"score": 80, "summary": "Ready to book."},
                "escalation": {"escalation_reason": "Requested a person."},
            },
            "token": {
                "input_tokens": 100,
                "output_tokens": 30,
                "thinking_tokens": 10,
                "cached_input_tokens": 5,
                "total_tokens": 130,
            },
            "cost": 0.000105,
        }
        client = object.__new__(AgentClient)
        client.chatbot = self.chatbot
        client.session = self.session

        with patch("agent.client.slots_for_agreed_date", return_value={
            "date": "2026-09-11",
            "timezone": "UTC+6.00",
            "slots": [{
                "starts_at": "2026-09-11T09:00:00+06:00",
                "ends_at": "2026-09-11T09:30:00+06:00",
            }],
        }):
            public_reply = client._persist_reply(
                f"ai:{self.visitor_message.id}",
                response,
            )

        self.assertEqual(public_reply, {
            "content": "Tomorrow is available.",
            "metadata": {
                "appointments": {
                    "date": "2026-09-11",
                    "timezone": "UTC+6.00",
                    "available_slots": [
                        {"start_time": "9.00 AM", "end_time": "9.30 AM"},
                    ],
                },
                "lead_analytics": {"score": 80, "summary": "Ready to book."},
                "escalation": {"reason": "Requested a person."},
                "sources": [{
                    "id": str(source.id),
                    "source_type": "website",
                    "name": "Booking FAQ",
                    "url": "https://example.com/booking",
                }],
            },
        })
        message = ChatMessage.objects.get(
            external_id=f"ai:{self.visitor_message.id}"
        )
        self.assertEqual(message.metadata, public_reply["metadata"])
        usage = AIUsage.objects.get(chat_message=message)
        self.assertEqual(usage.tokens, 130)
        self.assertEqual(usage.cost, Decimal("0.00010500"))
