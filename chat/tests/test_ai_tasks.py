from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from analytics.choices import AIUsageType
from analytics.models import AIUsage
from analytics.services.ai_usage import record_ai_usage
from chatbot.models import Chatbot
from chat.models import ChatMessage, ChatSession
from chat.tasks import generate_ai_reply_task
from chat.utils.choices import ChatMessageSenderType
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
    def test_agent_reply_saves_public_metadata_and_usage(self, agent_client):
        agent_client.return_value.chat_sync.return_value = {
            "result": {
                "content": "Tomorrow is available.",
                "source_ids": ["source-1"],
                "appointment": {
                    "status": "available",
                    "available": True,
                    "date": "2026-09-11",
                },
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

        result = generate_ai_reply_task.apply(
            args=[str(self.visitor_message.id)],
            throw=True,
        )

        reply = ChatMessage.objects.get(pk=result.result)
        self.assertEqual(reply.content, "Tomorrow is available.")
        self.assertEqual(
            reply.metadata,
            {
                "in_reply_to": str(self.visitor_message.id),
                "source_ids": ["source-1"],
                "appointment": {
                    "status": "available",
                    "available": True,
                    "date": "2026-09-11",
                },
            },
        )
        agent_client.assert_called_once_with(self.chatbot, self.session)
        agent_client.return_value.chat_sync.assert_called_once_with(
            message="Can I book tomorrow?",
            user_id="visitor-1",
        )

        usage = AIUsage.objects.get(chat_message=reply)
        self.assertEqual(usage.chatbot, self.chatbot)
        self.assertEqual(usage.chat_session, self.session)
        self.assertEqual(usage.tokens, 130)
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.output_tokens, 30)
        self.assertEqual(usage.thinking_tokens, 10)
        self.assertEqual(usage.cached_input_tokens, 5)
        self.assertEqual(usage.cost, Decimal("0.00010500"))
        self.assertEqual(usage.model, "gemini-2.5-flash")

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

    @patch("chat.tasks.AgentClient")
    def test_usage_survives_session_and_chatbot_deletion(self, agent_client):
        agent_client.return_value.chat_sync.return_value = {
            "result": {
                "content": "Reply",
                "source_ids": [],
                "appointment": None,
            },
            "token": {"total_tokens": 12},
            "cost": 0.0001,
        }
        result = generate_ai_reply_task.apply(
            args=[str(self.visitor_message.id)],
            throw=True,
        )
        usage = AIUsage.objects.get(chat_message_id=result.result)
        chatbot_id = self.chatbot.id
        session_id = self.session.id
        message_id = usage.chat_message_id

        self.session.delete()
        usage.refresh_from_db()
        self.assertIsNone(usage.chat_session)
        self.assertIsNone(usage.chat_message)
        self.assertEqual(usage.chat_session_id_snapshot, session_id)
        self.assertEqual(usage.chat_message_id_snapshot, message_id)

        self.chatbot.delete()
        usage.refresh_from_db()
        self.assertIsNone(usage.chatbot)
        self.assertEqual(usage.chatbot_id_snapshot, chatbot_id)
