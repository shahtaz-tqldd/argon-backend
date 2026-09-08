import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
import django
django.setup()

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from agent.client import AgentClient
from agent.helpers.load_instruction import load_sub_agent_instruction
from agent.helpers.tools import create_tools
from agent.summary import generate_conversation_summary
from agent.utils.schema import ConversationAnalysis


class FakeModel(BaseLlm):
    model: str = "fake"

    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Hello")]))


def chatbot():
    return SimpleNamespace(id="bot-1", ai_enabled=True, is_active=True,
        knowledge_base_enabled=True, chatbot_name="Test", business_name="Business",
        language="en", timezone="UTC", instructions="", never_answer="",
        escalation_rule="", fallback_message="Unknown")


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_runs_real_adk_and_isolates_tenants(self):
        from google.adk.sessions import InMemorySessionService
        sessions = InMemorySessionService()
        client = AgentClient(model=FakeModel(), session_service=sessions)
        bot = chatbot()
        self.assertEqual(await client.call(chatbot=bot, user_id="u", session_id="s", message="Hi"), "Hello")
        session = await sessions.get_session(app_name=client.app_name, user_id="bot-1:u", session_id="s")
        self.assertEqual(len(session.events), 2)
        bot.id = "bot-2"
        await client.call(chatbot=bot, user_id="u", session_id="s", message="Hi")
        session2 = await sessions.get_session(app_name=client.app_name, user_id="bot-2:u", session_id="s")
        self.assertEqual(len(session2.events), 2)

    async def test_knowledge_scope_and_disabled(self):
        bot = chatbot()
        service = Mock()
        service.search.return_value = [SimpleNamespace(knowledge_base_id="source", content="Fact")]
        search, *_ = create_tools(bot, "session", vector_service=service)
        self.assertEqual((await search("question"))["sources"][0]["content"], "Fact")
        service.search.assert_called_once_with("question", chatbot_id="bot-1", limit=6)
        bot.knowledge_base_enabled = False
        self.assertEqual((await search("question"))["status"], "disabled")
        self.assertEqual(service.search.call_count, 1)

    async def test_booking_requires_confirmation_and_valid_json(self):
        *_, book = create_tools(chatbot(), "s")
        with patch("agent.helpers.booking.create_booking") as save:
            self.assertEqual((await book("2030-01-01", "{}", False))["status"], "confirmation_required")
            self.assertEqual((await book("2030-01-01", "[]", True))["status"], "invalid")
            save.assert_not_called()

    async def test_analysis_threshold_and_no_automatic_call(self):
        with patch("agent.summary._closed_transcript", return_value=None) as transcript:
            self.assertIsNone(await generate_conversation_summary("s", min_user_messages=7))
            transcript.assert_called_once_with("s", 7)
        with self.assertRaises(ValueError):
            await generate_conversation_summary("s", min_user_messages=0)

    def test_score_bounds(self):
        with self.assertRaises(ValueError):
            ConversationAnalysis(summary="test", lead_score=101, lead_score_reason="test")


class InstructionTests(unittest.TestCase):
    def test_loads_instruction_from_sub_agent_directory(self):
        expected = (
            Path(__file__).resolve().parent.parent
            / "sub_agents"
            / "knowledge"
            / "instruction.txt"
        ).read_text(encoding="utf-8")

        self.assertEqual(load_sub_agent_instruction("knowledge"), expected)

    def test_rejects_paths_outside_sub_agents_directory(self):
        with self.assertRaises(ValueError):
            load_sub_agent_instruction("../knowledge")


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timezone
        self.now = datetime(2026, 9, 7, 8, tzinfo=timezone.utc)
        self.config = SimpleNamespace(
            chatbot=SimpleNamespace(timezone="UTC"), chatbot_id="bot-1",
            is_enabled=True, maximum_advance_days=30,
            max_appointments_per_day=None, appointment_duration_minutes=30,
            closed_dates=Mock(), schedules=Mock(),
        )
        self.config.closed_dates.filter.return_value.exists.return_value = False
        from datetime import time
        schedule = Mock()
        schedule.slots.filter.return_value = [SimpleNamespace(start_time=time(9), end_time=time(10))]
        self.config.schedules.filter.return_value = [schedule]

    def test_overlap_removed_and_duration_respected(self):
        from datetime import datetime, timezone
        from agent.helpers.booking import available_slots
        existing = SimpleNamespace(starts_at=datetime(2026, 9, 7, 9, 15, tzinfo=timezone.utc),
                                   ends_at=datetime(2026, 9, 7, 9, 30, tzinfo=timezone.utc))
        with patch("agent.helpers.booking.Appointment.objects.filter", return_value=[existing]):
            slots = available_slots(self.config, self.now.date(), now=self.now)
        self.assertEqual(slots, [{"starts_at": "2026-09-07T09:30:00+00:00", "ends_at": "2026-09-07T10:00:00+00:00"}])

    def test_daily_limit_closed_date_and_advance_limit(self):
        from datetime import timedelta
        from agent.helpers.booking import available_slots
        self.config.max_appointments_per_day = 1
        with patch("agent.helpers.booking.Appointment.objects.filter", return_value=[Mock()]):
            self.assertEqual(available_slots(self.config, self.now.date(), now=self.now), [])
        self.config.closed_dates.filter.return_value.exists.return_value = True
        self.assertEqual(available_slots(self.config, self.now.date(), now=self.now), [])
        self.assertEqual(available_slots(self.config, self.now.date() + timedelta(days=31), now=self.now), [])

    def test_summary_counts_only_visitors_and_requires_closed(self):
        from agent.summary import _closed_transcript
        session = Mock(status="open")
        with patch("chat_session.models.ChatSession.objects.get", return_value=session):
            self.assertIsNone(_closed_transcript("s", 5))
            session.messages.filter.assert_not_called()
            session.status = "closed"
            session.messages.filter.return_value.count.return_value = 4
            self.assertIsNone(_closed_transcript("s", 5))
            session.messages.filter.assert_called_once_with(sender_type="visitor")
