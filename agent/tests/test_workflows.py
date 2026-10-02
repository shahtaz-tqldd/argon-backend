import asyncio
import json
from datetime import datetime, time, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase, override_settings
from google.adk.models import BaseLlm, LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field

from agent.client import AgentClient
from agent.schema import AppointmentAgentResponseSchema
from agent.sub_agents.appointment import tools as booking
from subscription.choices import PlanFeature


class ScriptedModel(BaseLlm):
    responses: list = Field(default_factory=list)
    requests: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request.model_copy(deep=True))
        if not self.responses:
            raise AssertionError("Unexpected extra model call")
        # A streamed answer arrives as consecutive partial chunks followed by
        # the completed response, all from one generator call.
        result = self.responses.pop(0)
        is_streaming_call = result.partial
        while True:
            for part in result.content.parts if result.content else []:
                if part.function_call and part.function_call.name not in llm_request.tools_dict:
                    raise AssertionError(f"Tool is not exposed to this agent: {part.function_call.name}")
            yield result
            if not is_streaming_call or not result.partial or not self.responses:
                break
            result = self.responses.pop(0)


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


def answer(content="Hello", source_ids=None, agreed_date=None):
    """A specialist's structured final answer."""
    return response(types.Part.from_text(text=json.dumps({
        "content": content,
        "source_ids": source_ids or [],
        **({"agreed_date": agreed_date} if agreed_date else {}),
    })))


def say(content="Hello"):
    """The coordinator's plain-text final answer."""
    return response(types.Part.from_text(text=content))


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
            "agent.sub_agents.appointment.tools",
            "agent.helpers.global_tools",
        ]:
            self.enterContext(patch(f"{module}.sync_to_async", side_effect=inline_async))
        self.bot = SimpleNamespace(
            id="bot-1", chatbot_name="Assistant", business_name="Business",
            timezone="Asia/Dhaka", language="en", instructions="", never_answer="",
            escalation_rule="", fallback_message="Please try again.", ai_enabled=True,
            is_active=True, knowledge_base_enabled=True,
            human_handoff_enabled=True, appointment_booking_enabled=True,
            capacity=SimpleNamespace(
                active_features=[PlanFeature.LEAD_CAPTURE],
                has_feature=lambda feature: feature == PlanFeature.LEAD_CAPTURE,
            ),
        )
        self.conversation = SimpleNamespace(id="session-1", chatbot_id="bot-1", is_test=False, messages=Mock())
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
        # Global policy is rendered when the client is constructed.
        self.client = AgentClient(self.bot, self.conversation, session_service=self.service)
        model = self.script(call("knowledge_agent", request="Find the opening hours"),
                            call("search_knowledge", query="opening hours"),
                            answer("Open at 9", ["kb-2"]),
                            say("We open at 9."))
        matches = [SimpleNamespace(knowledge_base_id=s, content="Open at 9") for s in ["kb-1", "kb-2"]]
        with patch("agent.sub_agents.knowledge.tools.KnowledgeVectorService.search", return_value=matches) as search:
            result = await self.client._run_turn("When do you open?", user_id="visitor")
        self.assertEqual(result["result"]["source_ids"], ["kb-2"])
        self.assertEqual(result["result"]["content"], "We open at 9.")
        self.assertEqual(result["token"], dict(input_tokens=400, output_tokens=100,
                         thinking_tokens=20, cached_input_tokens=40, total_tokens=500))
        self.assertAlmostEqual(result["cost"], 0.00037)
        search.assert_called_once_with("opening hours", chatbot_id="bot-1", limit=5)
        self.assertEqual(len(model.requests), 4)
        for request in model.requests:
            self.assertIn(self.bot.never_answer, request.config.system_instruction)
            self.assertEqual(
                request.config.system_instruction.count(
                    "You are Assistant, the customer-facing AI assistant for Business."
                ),
                1,
            )
        session = await self.service.get_session(app_name=self.client.app_name,
                                                 user_id="bot-1:visitor", session_id="session-1")
        self.assertIsNotNone(session)

    def test_app_and_coordinator_configuration(self):
        root = self.client.chat_agent
        self.assertEqual(root.mode, "chat")
        self.assertIsNone(root.output_schema)
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
        self.assertEqual([tool.name for tool in root.sub_agents[0].tools],
                         ["search_knowledge", "record_lead_score", "request_human_escalation"])
        self.assertEqual([tool.name for tool in root.sub_agents[1].tools],
                         ["find_appointment_availability", "record_lead_score",
                          "request_human_escalation"])

    async def test_logs_when_appointment_intent_is_not_delegated(self):
        self.script(say(self.bot.fallback_message))

        with self.assertLogs("app", level="INFO") as captured:
            result = await self.client._run_turn(
                "Hi, I need to book an appointment.",
                None,
            )

        self.assertEqual(result["result"]["content"], self.bot.fallback_message)
        logs = "\n".join(captured.output)
        self.assertIn("appointment_intent_hint=True", logs)
        self.assertIn("available_specialists=['appointment_agent', 'knowledge_agent']", logs)
        self.assertIn("Appointment intent was not delegated", logs)

    async def test_uses_specialist_outcome_when_coordinator_returns_no_text(self):
        self.script(
            call("appointment_agent", request="Ask for the visitor's preferred date"),
            answer("Which date would you prefer?"),
            say(""),
        )

        with self.assertLogs("app", level="INFO") as captured:
            result = await self.client._run_turn(
                "Hi, I need to book an appointment.",
                None,
            )

        self.assertEqual(result["result"]["content"], "Which date would you prefer?")
        self.assertIn("response_source=specialist", "\n".join(captured.output))

    async def test_available_tool_result_attaches_slots_when_model_omits_agreed_date(self):
        availability = {
            "requested_date": "2026-10-05",
            "is_available_on_requested_date": True,
            "timezone": "UTC+6.00",
            "available_slots": 2,
        }
        slots = {
            "status": "available",
            "available": True,
            "requested_date": "2026-10-05",
            "date": "2026-10-05",
            "timezone": "UTC+6.00",
            "available_count": 1,
            "slots": [{
                "starts_at": "2026-10-05T09:00:00+06:00",
                "ends_at": "2026-10-05T09:30:00+06:00",
            }],
        }
        self.script(
            call("appointment_agent", request="Check next Monday"),
            call("find_appointment_availability", requested_date="2026-10-05"),
            answer("Monday is available. Would you like to proceed?"),
            say("Monday is available. Would you like to proceed?"),
        )

        with patch(
            "agent.sub_agents.appointment.tools.find_appointment_slot_counts",
            return_value=availability,
        ), patch(
            "agent.client.slots_for_agreed_date",
            return_value=slots,
        ) as attach, patch.object(self.client, "_source_metadata", return_value=[]):
            result = await self.client._run_turn("Monday?", None)
            public_reply = self.client._public_reply(result)

        self.assertEqual(result["result"]["agreed_date"], "2026-10-05")
        self.assertEqual(
            public_reply["metadata"]["appointments"]["date"],
            "2026-10-05",
        )
        attach.assert_called_once_with("bot-1", "2026-10-05")

    def test_appointment_handoff_date_is_validated_and_json_serializable(self):
        result = AppointmentAgentResponseSchema.model_validate({
            "content": "Monday is available.",
            "agreed_date": "2026-10-05",
        })

        self.assertEqual(result.agreed_date, "2026-10-05")
        self.assertEqual(
            json.loads(json.dumps(result.model_dump()))["agreed_date"],
            "2026-10-05",
        )
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            AppointmentAgentResponseSchema.model_validate({
                "content": "That date is invalid.",
                "agreed_date": "2026-02-30",
            })

    def test_composition_follows_chatbot_features(self):
        self.bot.knowledge_base_enabled = False
        knowledge_off = AgentClient(self.bot, self.conversation,
                                    session_service=InMemorySessionService())
        self.assertEqual([a.name for a in knowledge_off.chat_agent.sub_agents],
                         ["appointment_agent"])
        self.assertEqual([t.name for t in knowledge_off.chat_agent.tools],
                         ["record_lead_score", "request_human_escalation",
                          "appointment_agent"])

        self.bot.appointment_booking_enabled = False
        bare = AgentClient(self.bot, self.conversation,
                           session_service=InMemorySessionService())
        self.assertEqual(bare.chat_agent.sub_agents, [])
        self.assertEqual([t.name for t in bare.chat_agent.tools],
                         ["record_lead_score", "request_human_escalation"])

    def test_escalation_tool_hidden_when_handoff_disabled(self):
        self.bot.human_handoff_enabled = False
        client = AgentClient(self.bot, self.conversation,
                             session_service=InMemorySessionService())
        knowledge = client.chat_agent.sub_agents[0]
        self.assertEqual([t.name for t in knowledge.tools],
                         ["search_knowledge", "record_lead_score"])
        instruction = client.app.plugins[0].global_instruction(None)
        self.assertNotIn("request_human_escalation", instruction)
        self.assertNotIn("Escalation triggers:", instruction)

    def test_global_identity_and_greeting_are_business_facing(self):
        instruction = self.client.app.plugins[0].global_instruction(None)

        self.assertIn(
            'For a greeting-only message, reply: "Hey, I am Assistant. '
            'I am here to help with Business. How may I help you?"',
            instruction,
        )
        self.assertIn("Never identify yourself as Gemini", instruction)
        self.assertIn(
            "If asked who or what you are, state only the business-facing identity",
            instruction,
        )

    def test_lead_score_tool_and_instruction_hidden_when_feature_disabled(self):
        self.bot.capacity.active_features = []
        self.bot.capacity.has_feature = lambda _feature: False
        client = AgentClient(self.bot, self.conversation,
                             session_service=InMemorySessionService())

        self.assertEqual([t.name for t in client.chat_agent.sub_agents[0].tools],
                         ["search_knowledge", "request_human_escalation"])
        instruction = client.app.plugins[0].global_instruction(None)
        self.assertNotIn("Lead scoring:", instruction)

    async def test_mixed_request_delegates_to_both_specialists(self):
        slots = [{
            "starts_at": "2026-06-16T09:00:00+06:00",
            "ends_at": "2026-06-16T09:30:00+06:00",
        }]
        payload = dict(requested_date="2026-06-16",
                       is_available_on_requested_date=True,
                       timezone="UTC+6.00", available_slots=1)
        slot_payload = dict(status="available", available=True,
                            requested_date="2026-06-16", date="2026-06-16",
                            timezone="UTC+6.00", available_count=1, slots=slots)
        model = self.script(
            call("knowledge_agent", request="Which service is offered?"),
            call("search_knowledge", query="services"), answer("Consultations", ["kb-1"]),
            call("appointment_agent", request="Book a consultation on June 16"),
            call("find_appointment_availability", requested_date="2026-06-16"),
            answer("June 16 is available.", agreed_date="2026-06-16"),
            say("We offer consultations. Select a June 16 slot in the UI."),
        )
        with patch("agent.sub_agents.knowledge.tools.KnowledgeVectorService.search", return_value=[
            SimpleNamespace(knowledge_base_id="kb-1", content="Consultations")
        ]), patch(
            "agent.sub_agents.appointment.tools.find_appointment_slot_counts",
            return_value=payload,
        ), patch(
            "agent.client.slots_for_agreed_date", return_value=slot_payload,
        ), patch.object(self.client, "_source_metadata", return_value=[]):
            result = await self.client._run_turn("What service can I book on June 16?", None)
            public_reply = self.client._public_reply(result)
        self.assertEqual(result["result"]["source_ids"], ["kb-1"])
        self.assertEqual(result["result"]["agreed_date"], "2026-06-16")
        self.assertEqual(public_reply["metadata"]["appointments"], {
            "date": "2026-06-16",
            "timezone": "UTC+6.00",
            "available_slots": [
                {"start_time": "9.00 AM", "end_time": "9.30 AM"},
            ],
        })
        tool_responses = [part.function_response.response
                          for request in model.requests for content in request.contents
                          for part in content.parts or []
                          if part.function_response and part.function_response.name == "find_appointment_availability"]
        self.assertTrue(tool_responses)
        self.assertTrue(all("slots" not in payload for payload in tool_responses))
        self.assertEqual(result["token"]["total_tokens"], 7 * 125)
        self.assertEqual(len(model.requests), 7)

    async def test_runner_shares_vertex_client_per_turn_and_closes_between_turns(self):
        from agent.helpers.model import vertex_client

        original_generate = ScriptedModel.generate_content_async
        seen_clients = []

        async def generate(model, llm_request, stream=False):
            seen_clients.append(vertex_client())
            async for item in original_generate(model, llm_request, stream):
                yield item

        clients = [Mock(aio=Mock(aclose=AsyncMock())), Mock(aio=Mock(aclose=AsyncMock()))]
        self.script(
            call("appointment_agent", request="Ask for preferred date"),
            answer("Which date would you prefer?"), say("Which date would you prefer?"),
            call("appointment_agent", request="Clarify the requested month"),
            answer("Which month?"), say("Which month?"),
        )
        with patch("agent.helpers.model.Client", side_effect=clients) as factory, patch.object(
            ScriptedModel, "generate_content_async", generate
        ):
            await self.client._run_turn("I'd like an appointment", None)
            clients[0].aio.aclose.assert_awaited_once()
            clients[0].close.assert_called_once()
            await self.client._run_turn("On the 15th", None)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(seen_clients, [clients[0]] * 3 + [clients[1]] * 3)
        clients[1].aio.aclose.assert_awaited_once()
        clients[1].close.assert_called_once()

    async def test_context_answer_cites_previously_retrieved_sources(self):
        matches = [SimpleNamespace(knowledge_base_id="kb-1", content="Open at 9")]
        self.script(
            call("knowledge_agent", request="Find the opening hours"),
            call("search_knowledge", query="opening hours"),
            answer("Open at 9", ["kb-1"]),
            say("We open at 9."),
            # Second turn answers from conversation context without a search.
            call("knowledge_agent", request="Confirm the opening hours already discussed"),
            answer("Still open at 9", ["kb-1"]),
            say("Still open at 9."),
        )
        with patch("agent.sub_agents.knowledge.tools.KnowledgeVectorService.search", return_value=matches) as search:
            first = await self.client._run_turn("When do you open?", None)
            second = await self.client._run_turn("Right, and you said?", None)
        search.assert_called_once()
        self.assertEqual(first["result"]["source_ids"], ["kb-1"])
        self.assertEqual(second["result"]["source_ids"], ["kb-1"])

    async def test_alternative_is_attached_only_after_visitor_agrees(self):
        unavailable = {
            "requested_date": "2026-06-16",
            "is_available_on_requested_date": False,
            "timezone": "UTC+6.00",
            "alternative_dates": [
                {"date": "2026-06-18", "available_slots": 2},
            ],
        }
        slot_payload = {
            "status": "available", "available": True,
            "requested_date": "2026-06-18", "date": "2026-06-18",
            "timezone": "UTC+6.00", "available_count": 1,
            "slots": [{
                "starts_at": "2026-06-18T09:00:00+06:00",
                "ends_at": "2026-06-18T09:30:00+06:00",
            }],
        }
        self.script(
            call("appointment_agent", request="Find June 16 availability"),
            call("find_appointment_availability", requested_date="2026-06-16"),
            answer("June 16 is unavailable. Would June 18 work?"),
            say("June 16 is unavailable. Would June 18 work?"),
            call("appointment_agent", request="Visitor accepts June 18"),
            answer("June 18 works.", agreed_date="2026-06-18"),
            say("June 18 works."),
        )
        with patch(
            "agent.sub_agents.appointment.tools.find_appointment_slot_counts",
            return_value=unavailable,
        ) as search, patch(
            "agent.client.slots_for_agreed_date", return_value=slot_payload,
        ) as attach, patch.object(self.client, "_source_metadata", return_value=[]):
            offered = await self.client._run_turn("June 16 please", None)
            agreed = await self.client._run_turn("Yes, June 18", None)
            offered_public = self.client._public_reply(offered)
            agreed_public = self.client._public_reply(agreed)
        search.assert_called_once_with("bot-1", "2026-06-16")
        self.assertIsNone(offered["result"]["agreed_date"])
        self.assertNotIn("appointments", offered_public["metadata"])
        self.assertEqual(agreed["result"]["agreed_date"], "2026-06-18")
        self.assertEqual(
            agreed_public["metadata"]["appointments"]["date"],
            "2026-06-18",
        )
        attach.assert_called_once_with("bot-1", "2026-06-18")

    async def test_booking_confirmation_uses_invocation_context_and_runner_memory(self):
        payload = dict(status="booking_recorded", available=False, appointment_id="appt-1",
                       appointment_status="pending", starts_at="2026-06-18T09:00:00+06:00",
                       ends_at="2026-06-18T09:30:00+06:00")
        model = self.script(call("appointment_agent", request="Acknowledge the backend booking event"),
                            answer("Your request is awaiting approval."),
                            say("Your request is awaiting approval."),
                            call("appointment_agent", request="Visitor asks whether their booking is confirmed"),
                            answer("No new booking event."),
                            say("No new booking event."))
        message = json.dumps({"event": "booking_confirmation", "booking": payload})
        result = await self.client._run_turn(message, None, confirmation=payload)
        self.assertIsNone(result["result"]["agreed_date"])
        session = await self.client._get_or_create_session("bot-1:session-1")
        self.assertNotIn("booking_confirmations", session.state)
        self.assertTrue(any("booking_confirmation" in str(e.content) for e in session.events))
        self.assertIn('"appointment_status": "pending"', model.requests[1].config.system_instruction)
        await self.client._run_turn("Is my booking confirmed?", None)
        self.assertIn("Backend-verified booking event for this turn: null", model.requests[4].config.system_instruction)
        names = [tool.name for tool in self.client.chat_agent.tools]
        self.assertEqual(names, ["record_lead_score", "request_human_escalation",
                                 "knowledge_agent", "appointment_agent"])

    async def test_specialist_records_lead_score_during_conversation(self):
        self.script(
            call("knowledge_agent", request="Visitor needs this this month and wants a call"),
            call(
                "record_lead_score",
                score=82,
                summary="Needs implementation this month and requested a booking.",
            ),
            answer("I can help you schedule that."),
            say("I can help you schedule that."),
        )
        payload = {
            "score": 82,
            "summary": "Needs implementation this month and requested a booking.",
            "recorded": True,
        }
        with patch("agent.helpers.global_tools._record_lead_score", return_value=payload) as record:
            result = await self.client._run_turn("We need this this month. Can we book a call?", None)
        record.assert_called_once_with(
            "bot-1",
            "session-1",
            82,
            "Needs implementation this month and requested a booking.",
        )
        self.assertEqual(result["result"]["lead_score"], payload)

    async def test_specialist_escalation_is_returned_to_caller(self):
        self.script(
            call("knowledge_agent", request="Visitor wants a person"),
            call(
                "request_human_escalation",
                escalation_reason="Visitor explicitly asked to speak with a person.",
            ),
            answer("A human teammate will follow up."),
            say("A human teammate will follow up."),
        )
        payload = {
            "requires_attention": True,
            "escalation_reason": "Visitor explicitly asked to speak with a person.",
        }
        with patch("agent.helpers.global_tools._request_human_escalation", return_value=payload):
            result = await self.client._run_turn("Let me speak with a human", None)
        self.assertEqual(result["result"]["escalation"], payload)

    async def test_invalid_specialist_output_fails_closed(self):
        self.script(call("knowledge_agent", request="Find hours"),
                    response(types.Part.from_text(text="not JSON")))
        with patch("agent.sub_agents.knowledge.tools.KnowledgeVectorService.search", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "ValidationError"):
                await self.client._run_turn("Hours?", None)

    async def test_booking_context_is_not_persisted_after_model_failure(self):
        payload = dict(status="booking_recorded", appointment_id="appt-1", appointment_status="confirmed")
        self.script(LlmResponse(error_code="unavailable", error_message="Try later"))
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            await self.client._run_turn("booking event", None, confirmation=payload)
        session = await self.client._get_or_create_session("bot-1:session-1")
        self.assertNotIn("booking_confirmations", session.state)

    async def test_missing_knowledge_returns_empty_citations(self):
        self.script(call("knowledge_agent", request="Find hours"),
                    call("search_knowledge", query="hours"), answer("Unknown"),
                    say("Unknown"))
        with patch("agent.sub_agents.knowledge.tools.KnowledgeVectorService.search", return_value=[]):
            result = await self.client._run_turn("Hours?", None)
        self.assertEqual(result["result"]["source_ids"], [])

    def test_cross_chatbot_conversation_is_rejected(self):
        self.conversation.chatbot_id = "other-bot"
        with self.assertRaises(ValueError):
            AgentClient(self.bot, self.conversation, session_service=self.service)

    def test_public_reply_omits_metadata_sections_not_generated(self):
        response = {
            "result": {
                "content": "How can I help?",
                "source_ids": [],
                "agreed_date": None,
                "lead_score": None,
                "escalation": None,
            },
        }
        with patch.object(self.client, "_source_metadata", return_value=[]):
            self.assertEqual(
                self.client._public_reply(response),
                {"content": "How can I help?", "metadata": {}},
            )


class AvailabilityTests(SimpleTestCase):
    def test_booking_verification_scopes_tenant_conversation_and_status(self):
        with patch("agent.sub_agents.appointment.tools.Appointment.objects.filter") as query:
            query.return_value.first.return_value = None
            with self.assertRaises(ValueError):
                booking.verified_booking("bot", "session", "appointment")
        query.assert_called_once_with(
            pk="appointment",
            chatbot_id="bot",
            metadata__chat_session_id="session",
            status__in=("pending", "confirmed"),
        )

    def test_agreed_date_payload_adds_slots_in_code(self):
        config = SimpleNamespace(chatbot=SimpleNamespace(timezone="Asia/Dhaka"))
        slots = [{
            "starts_at": "2026-06-18T09:00:00+06:00",
            "ends_at": "2026-06-18T09:30:00+06:00",
        }]
        with patch(
            "agent.sub_agents.appointment.tools.get_appointment_booking_config",
            return_value=config,
        ), patch(
            "agent.sub_agents.appointment.tools.get_available_appointment_slots",
            return_value=slots,
        ):
            result = booking.slots_for_agreed_date("bot", "2026-06-18")
        self.assertEqual(result["date"], "2026-06-18")
        self.assertEqual(result["available_count"], 1)
        self.assertEqual(result["slots"], slots)
