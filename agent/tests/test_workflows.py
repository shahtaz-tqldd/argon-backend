import asyncio
import json
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from google.adk.models import BaseLlm, LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field

from agent.client import AgentClient
from agent.sub_agents.appointment.tools import booking


class ScriptedModel(BaseLlm):
    responses: list = Field(default_factory=list)
    requests: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request.model_copy(deep=True))
        if not self.responses:
            raise AssertionError("Unexpected extra model call")
        result = self.responses.pop(0)
        for part in result.content.parts if result.content else []:
            if part.function_call and part.function_call.name not in llm_request.tools_dict:
                raise AssertionError(f"Tool is not exposed to this agent: {part.function_call.name}")
        yield result


def response(*parts):
    return LlmResponse(
        content=types.Content(role="model", parts=list(parts)),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=20,
            thoughts_token_count=5, total_token_count=125,
            cached_content_token_count=10,
        ),
    )


def call(name, **args):
    return response(types.Part.from_function_call(name=name, args=args))


def answer(content="Hello", source_ids=None):
    return response(types.Part.from_text(text=json.dumps({
        "content": content,
        "source_ids": source_ids or [],
    })))


def inline_async(func, **kwargs):
    # All ORM/retrieval calls are mocks here; no worker threads or DB connections
    # are needed to exercise the real ADK runner, tools, events, and schemas.
    async def run(*args, **kw):
        await asyncio.sleep(0)
        return func(*args, **kw)
    return run


class ClientTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(override_settings(GEMINI_CHAT_MODEL="gemini-2.5-flash",
                          GEMINI_INPUT_COST_PER_MILLION=0.3, GEMINI_OUTPUT_COST_PER_MILLION=2.5))
        for module in [
            "agent.client",
            "agent.sub_agents.knowledge.tools",
            "agent.sub_agents.appointment.tools.availability",
            "agent.tools.conversation",
        ]:
            self.enterContext(patch(f"{module}.sync_to_async", side_effect=inline_async))
        self.bot = SimpleNamespace(
            id="bot-1", chatbot_name="Assistant", business_name="Business",
            timezone="Asia/Dhaka", language="en", instructions="", never_answer="",
            escalation_rule="", fallback_message="Please try again.", ai_enabled=True,
            is_active=True, knowledge_base_enabled=True,
        )
        self.conversation = SimpleNamespace(id="session-1", chatbot_id="bot-1", messages=Mock())
        self.service = InMemorySessionService()
        self.client = AgentClient(self.bot, self.conversation, session_service=self.service)

    def script(self, *responses):
        model = ScriptedModel(model="gemini-2.5-flash", responses=list(responses))
        self.client.chat_agent.model = model
        for specialist in self.client.chat_agent.sub_agents:
            specialist.model = model
        return model

    async def test_real_adk_tools_structured_output_usage_and_source_selection(self):
        self.bot.never_answer = "Never disclose employee payroll."
        model = self.script(call("knowledge_agent", request="Find the opening hours"),
                            call("search_knowledge", query="opening hours"),
                            answer("Open at 9", ["kb-2"]),
                            answer("We open at 9.", ["kb-2", "invented", "kb-2"]))
        matches = [SimpleNamespace(knowledge_base_id=s, content="Open at 9") for s in ["kb-1", "kb-2"]]
        with patch("agent.sub_agents.knowledge.tools.KnowledgeVectorService.search", return_value=matches) as search:
            result = await self.client.chat("When do you open?", user_id="visitor")
        self.assertEqual(result["result"]["source_ids"], ["kb-2"])
        self.assertEqual(result["result"]["content"], "We open at 9.")
        self.assertEqual(result["token"], dict(input_tokens=400, output_tokens=100,
                         thinking_tokens=20, cached_input_tokens=40, total_tokens=500))
        self.assertAlmostEqual(result["cost"], 0.00037)
        search.assert_called_once_with("opening hours", chatbot_id="bot-1", limit=5)
        self.assertEqual(len(model.requests), 4)
        for request in model.requests:
            self.assertIn(self.bot.never_answer, request.config.system_instruction)
        session = await self.service.get_session(app_name=self.client.app_name,
                                                user_id="bot-1:visitor", session_id="session-1")
        self.assertIsNotNone(session)

    def test_app_and_coordinator_configuration(self):
        root = self.client.chat_agent
        self.assertEqual(root.mode, "chat")
        self.assertIs(self.client.runner.app, self.client.app)
        self.assertIs(self.client.app.root_agent, root)
        self.assertEqual(self.client.app.events_compaction_config.compaction_interval, 3)
        self.assertEqual(self.client.app.events_compaction_config.overlap_size, 1)
        self.assertEqual(self.client.app.context_cache_config.min_tokens, 2048)
        self.assertEqual([agent.name for agent in root.sub_agents],
                         ["knowledge_agent", "appointment_agent"])
        self.assertTrue(all(agent.mode == "single_turn" for agent in root.sub_agents))
        self.assertEqual([tool.name for tool in root.tools],
                         ["record_lead_score", "request_human_escalation",
                          "knowledge_agent", "appointment_agent"])
        self.assertEqual([tool.name for tool in root.sub_agents[0].tools], ["search_knowledge"])
        self.assertEqual([tool.name for tool in root.sub_agents[1].tools], ["find_appointment_availability"])

    async def test_mixed_request_delegates_to_both_specialists(self):
        payload = dict(status="available", available=True, requested_date="2026-06-16", date="2026-06-16")
        model = self.script(
            call("knowledge_agent", request="Which service is offered?"),
            call("search_knowledge", query="services"), answer("Consultations", ["kb-1"]),
            call("appointment_agent", request="Book a consultation on June 16"),
            call("find_appointment_availability", requested_date="2026-06-16"),
            answer("June 16 is available."),
            answer("We offer consultations. Select a June 16 slot in the UI.", ["kb-1"]),
        )
        with patch("agent.sub_agents.knowledge.tools.KnowledgeVectorService.search", return_value=[
            SimpleNamespace(knowledge_base_id="kb-1", content="Consultations")
        ]), patch("agent.sub_agents.appointment.tools.booking.find_availability", return_value=payload):
            result = await self.client.chat("What service can I book on June 16?")
        self.assertEqual(result["result"]["source_ids"], ["kb-1"])
        self.assertEqual(result["result"]["appointment"]["date"], "2026-06-16")
        self.assertEqual(result["token"]["total_tokens"], 7 * 125)
        self.assertEqual(len(model.requests), 7)

    async def test_redelegating_does_not_reset_seven_day_search_budget(self):
        payload = dict(status="unavailable", available=False, date=None,
                       requested_date="2026-06-16", searched_through="2026-06-22")
        self.script(
            call("appointment_agent", request="Search June 16"),
            call("find_appointment_availability", requested_date="2026-06-16"), answer("No availability."),
            call("appointment_agent", request="Try June 23 too"),
            call("find_appointment_availability", requested_date="2026-06-23"), answer("No availability."),
            answer("Would you like to try next week?"),
        )
        with patch("agent.sub_agents.appointment.tools.booking.find_availability", return_value=payload) as search:
            result = await self.client.chat("Book June 16")
        search.assert_called_once_with("bot-1", "2026-06-16")
        self.assertEqual(result["result"]["appointment"]["searched_through"], "2026-06-22")

    async def test_search_budget_and_no_stale_appointment_on_next_turn(self):
        payload = dict(status="available", available=True, requested_date="2026-06-16",
                       date="2026-06-18", searched_through="2026-06-18", timezone="Asia/Dhaka")
        self.script(call("appointment_agent", request="Book June 16"),
                    call("find_appointment_availability", requested_date="2026-06-16"),
                    call("find_appointment_availability", requested_date="2026-06-23"),
                    answer("June 18 is available."), answer("June 18 is available."), answer("You're welcome"))
        with patch("agent.sub_agents.appointment.tools.booking.find_availability", return_value=payload) as search:
            result = await self.client.chat("Book June 16")
            next_turn = await self.client.chat("Thanks")
        search.assert_called_once_with("bot-1", "2026-06-16")
        self.assertTrue(result["result"]["appointment"]["available"])
        self.assertEqual(result["result"]["appointment"]["date"], "2026-06-18")
        self.assertIsNone(next_turn["result"]["appointment"])

    async def test_parallel_searches_cannot_expand_window_and_next_turn_can_search(self):
        payload = dict(status="unavailable", available=False, requested_date="2026-06-16",
                       date=None, searched_through="2026-06-22", next_search_date="2026-06-23")
        self.script(call("appointment_agent", request="Find June 16 availability"), response(
            types.Part.from_function_call(name="find_appointment_availability", args={"requested_date": "2026-06-16"}),
            types.Part.from_function_call(name="find_appointment_availability", args={"requested_date": "2026-06-23"}),
        ), answer("No availability. Try next week?"), answer("No availability. Try next week?"),
            call("appointment_agent", request="Visitor agreed to search June 23"),
            call("find_appointment_availability", requested_date="2026-06-23"),
            answer("No availability."), answer("No availability."))
        with patch("agent.sub_agents.appointment.tools.booking.find_availability", return_value=payload) as search:
            result = await self.client.chat("June 16 please")
            self.assertEqual(search.call_count, 1)
            self.assertEqual(result["result"]["appointment"]["status"], "unavailable")
            await self.client.chat("Yes, try next week")
        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args.args, ("bot-1", "2026-06-23"))

    async def test_booking_confirmation_is_recorded_and_not_created_by_llm(self):
        payload = dict(status="booking_recorded", available=False, appointment_id="appt-1",
                       appointment_status="pending", starts_at="2026-06-18T09:00:00+06:00",
                       ends_at="2026-06-18T09:30:00+06:00")
        model = self.script(call("appointment_agent", request="Acknowledge the backend booking event"),
                            answer("Your request is awaiting approval."), answer("Your request is awaiting approval."),
                            call("appointment_agent", request="Visitor asks whether their booking is confirmed"),
                            answer("No new booking event."), answer("No new booking event."))
        with patch("agent.client.verified_booking", return_value=payload) as verify:
            result = await self.client.confirm_booking("appt-1")
        verify.assert_called_once_with("bot-1", "session-1", "appt-1")
        self.assertEqual(result["result"]["appointment"]["appointment_status"], "pending")
        session = await self.client._get_or_create_session("bot-1:session-1")
        self.assertEqual(session.state["booking_confirmations"]["appt-1"], payload)
        self.assertTrue(any("booking_confirmation" in str(e.content) for e in session.events))
        self.assertIn('"appointment_status": "pending"', model.requests[1].config.system_instruction)
        await self.client.chat("Is my booking confirmed?")
        self.assertIn("Backend-verified booking event for this turn: null", model.requests[4].config.system_instruction)
        names = [tool.name for tool in self.client.chat_agent.tools]
        self.assertEqual(names, ["record_lead_score", "request_human_escalation",
                                 "knowledge_agent", "appointment_agent"])

    async def test_agent_records_lead_score_during_conversation(self):
        self.script(
            call(
                "record_lead_score",
                score=82,
                summary="Needs implementation this month and requested a booking.",
            ),
            answer("I can help you schedule that."),
        )
        payload = {
            "score": 82,
            "summary": "Needs implementation this month and requested a booking.",
            "recorded": True,
        }
        with patch("agent.tools.conversation._record_lead_score", return_value=payload) as record:
            result = await self.client.chat("We need this this month. Can we book a call?")
        record.assert_called_once_with(
            "bot-1",
            "session-1",
            82,
            "Needs implementation this month and requested a booking.",
        )
        self.assertEqual(result["result"]["lead_score"], payload)

    async def test_agent_escalation_is_returned_to_caller(self):
        self.script(
            call(
                "request_human_escalation",
                escalation_reason="Visitor explicitly asked to speak with a person.",
            ),
            answer("I have requested human assistance."),
        )
        payload = {
            "requires_attention": True,
            "escalation_reason": "Visitor explicitly asked to speak with a person.",
        }
        with patch("agent.tools.conversation._request_human_escalation", return_value=payload):
            result = await self.client.chat("Let me speak with a human")
        self.assertEqual(result["result"]["escalation"], payload)

    async def test_invalid_chat_output_fails_closed(self):
        self.script(response(types.Part.from_text(text="not JSON")))
        with self.assertRaisesRegex(RuntimeError, "invalid structured"):
            await self.client.chat("Hello")

    async def test_booking_event_survives_model_failure(self):
        payload = dict(status="booking_recorded", appointment_id="appt-1", appointment_status="confirmed")
        self.script(LlmResponse(error_code="unavailable", error_message="Try later"))
        with patch("agent.client.verified_booking", return_value=payload):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                await self.client.confirm_booking("appt-1")
        session = await self.client._get_or_create_session("bot-1:session-1")
        self.assertEqual(session.state["booking_confirmations"]["appt-1"], payload)

    async def test_missing_knowledge_returns_empty_citations(self):
        self.script(call("knowledge_agent", request="Find hours"),
                    call("search_knowledge", query="hours"), answer("Unknown"),
                    answer("Unknown", ["stale-source"]))
        with patch("agent.sub_agents.knowledge.tools.KnowledgeVectorService.search", return_value=[]):
            result = await self.client.chat("Hours?")
        self.assertEqual(result["result"]["source_ids"], [])

    def test_cross_chatbot_conversation_is_rejected(self):
        self.conversation.chatbot_id = "other-bot"
        with self.assertRaises(ValueError):
            AgentClient(self.bot, self.conversation, session_service=self.service)


class AvailabilityTests(SimpleTestCase):
    def setUp(self):
        self.config = SimpleNamespace(chatbot=SimpleNamespace(timezone="Asia/Dhaka"), maximum_advance_days=30)
        self.now = datetime(2026, 6, 15, 20, tzinfo=timezone.utc)  # June 16 locally.
        self.config_patch = patch("agent.sub_agents.appointment.tools.booking.get_config", return_value=self.config)
        self.now_patch = patch("agent.sub_agents.appointment.tools.booking.timezone.now", return_value=self.now)
        self.config_patch.start()
        self.now_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.now_patch.stop)

    def test_stops_on_first_available_day(self):
        with patch("agent.sub_agents.appointment.tools.booking.available_slots", side_effect=[[], [], [{"slot": 1}]]) as slots:
            result = booking.find_availability("bot", "2026-06-16")
        self.assertEqual(result["date"], "2026-06-18")
        self.assertTrue(result["available"])
        self.assertEqual([c.args[1] for c in slots.call_args_list], [date(2026, 6, d) for d in (16, 17, 18)])

    def test_searches_exactly_seven_days_including_preferred(self):
        with patch("agent.sub_agents.appointment.tools.booking.available_slots", return_value=[]) as slots:
            result = booking.find_availability("bot", "2026-06-16")
        self.assertEqual(slots.call_count, 7)
        self.assertEqual(result["searched_through"], "2026-06-22")
        self.assertEqual(result["next_search_date"], "2026-06-23")
        self.assertFalse(result["available"])

    def test_requested_day_available(self):
        with patch("agent.sub_agents.appointment.tools.booking.available_slots", return_value=[{}]) as slots:
            result = booking.find_availability("bot", "2026-06-16")
        self.assertEqual(result["date"], "2026-06-16")
        self.assertEqual(slots.call_count, 1)

    def test_invalid_past_and_beyond_horizon_never_query_slots(self):
        with patch("agent.sub_agents.appointment.tools.booking.available_slots") as slots:
            for day in ["garbage", "2026-02-30", "20260616", "2026-06-15", "2027-01-01"]:
                self.assertEqual(booking.find_availability("bot", day)["status"], "invalid")
        slots.assert_not_called()

    def test_horizon_truncates_search_without_offering_next_week(self):
        self.config.maximum_advance_days = 2
        with patch("agent.sub_agents.appointment.tools.booking.available_slots", return_value=[]) as slots:
            result = booking.find_availability("bot", "2026-06-16")
        self.assertEqual(slots.call_count, 3)
        self.assertIsNone(result["next_search_date"])
        self.assertEqual(result["searched_through"], "2026-06-18")

    def test_disabled_booking(self):
        with patch("agent.sub_agents.appointment.tools.booking.get_config", return_value=None), patch("agent.sub_agents.appointment.tools.booking.available_slots") as slots:
            self.assertEqual(booking.find_availability("bot", "2026-06-16")["status"], "disabled")
        slots.assert_not_called()

    def test_booking_verification_scopes_tenant_conversation_and_status(self):
        with patch("agent.sub_agents.appointment.tools.booking.Appointment.objects.filter") as query:
            query.return_value.first.return_value = None
            with self.assertRaises(ValueError):
                booking.verified_booking("bot", "session", "appointment")
        query.assert_called_once_with(pk="appointment", chatbot_id="bot",
                                      metadata__chat_session_id="session", status__in=("pending", "confirmed"))

    def test_slots_exclude_past_overlap_and_observe_daily_limit(self):
        config = SimpleNamespace(
            chatbot_id="bot", chatbot=SimpleNamespace(timezone="Asia/Dhaka"),
            is_enabled=True, maximum_advance_days=30, max_appointments_per_day=None,
            appointment_duration_minutes=30, closed_dates=Mock(), schedules=Mock(),
        )
        config.closed_dates.filter.return_value.exists.return_value = False
        schedule = Mock()
        schedule.slots.filter.return_value = [SimpleNamespace(start_time=time(9), end_time=time(11))]
        config.schedules.filter.return_value = [schedule]
        existing = SimpleNamespace(starts_at=datetime.fromisoformat("2026-06-16T09:30:00+06:00"),
                                   ends_at=datetime.fromisoformat("2026-06-16T10:00:00+06:00"))
        with patch("agent.sub_agents.appointment.tools.booking.Appointment.objects.filter", return_value=[existing]):
            slots = booking.available_slots(config, date(2026, 6, 16),
                        now=datetime.fromisoformat("2026-06-16T09:00:00+06:00"))
            self.assertEqual([s["starts_at"] for s in slots],
                             ["2026-06-16T10:00:00+06:00", "2026-06-16T10:30:00+06:00"])
            config.max_appointments_per_day = 1
            self.assertEqual(booking.available_slots(config, date(2026, 6, 16), now=self.now), [])
