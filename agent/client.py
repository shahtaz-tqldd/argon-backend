import json
from contextlib import aclosing
from uuid import uuid4

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from google.adk.agents.context import Context
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
from google.adk.workflow import START, Workflow, node
from google.genai import types
from pydantic import ValidationError

from agent.root_agent import root_agent
from agent.sub_agents.appointment.tools.booking import conversation_bookings, verified_booking
from agent.sub_agents.lead_summary.agent import lead_summary_agent
from agent.utils.schema import (
    AgentResponseSchema, AgentResultSchema, AppointmentSchema,
    KnowledgeBaseAgentOutputSchema, LeadSummaryAgentOutputSchema, TokenUsageSchema,
)


from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.events import Event
from google.adk.events.event_actions import EventActions



class AgentClient:
    """Caller authorizes access, persists Django replies, and serializes session turns."""

    app_name = "argon_agents"
    MESSAGE_TEXT_LIMIT = 10_000
    MIN_TOKENS_FOR_CONTEXT = 2048
    CACHE_TTL_SECONDS = 600
    CACHE_INTERVALS = 5
    COMPACTION_INTERVAL = 3
    COMPACTION_OVERLAP = 1

    def __init__(self, chatbot, session, *, session_service=None):
        if str(session.chatbot_id) != str(chatbot.id):
            raise ValueError("The conversation does not belong to this chatbot.")
        if session_service is None:
            if not settings.ADK_DB_URL:
                raise ImproperlyConfigured("Set ADK_DB_URL for persistent agent sessions.")
            session_service = DatabaseSessionService(db_url=settings.ADK_DB_URL)
        self.session_service = session_service
        self.chatbot = chatbot
        self.session = session
        self.chat_agent = root_agent(chatbot, session)
        self.app = App(
            name=self.app_name,
            root_agent=self.chat_agent,
            events_compaction_config=EventsCompactionConfig(
                compaction_interval=self.COMPACTION_INTERVAL,
                overlap_size=self.COMPACTION_OVERLAP,
            ),
            context_cache_config=ContextCacheConfig(
                min_tokens=self.MIN_TOKENS_FOR_CONTEXT,
                ttl_seconds=self.CACHE_TTL_SECONDS,
                cache_intervals=self.CACHE_INTERVALS,
            ),
        )
        self.runner = Runner(app=self.app, session_service=self.session_service)
    

    @staticmethod
    def _create_runner(agent, *, app_name, session_service, state_delta=None):
        @node
        async def initialize_turn(ctx: Context):
            # The node runtime in ADK 2.1 does not forward Runner.state_delta.
            # Commit trusted backend state before invoking any LLM node instead.
            for key, value in (state_delta or {}).items():
                ctx.state[key] = value
            return ctx.user_content

        workflow = Workflow(
            name=f"{agent.name}_workflow",
            edges=[(START, initialize_turn), (initialize_turn, agent)],
        )
        return Runner(agent=workflow, app_name=app_name, session_service=session_service)

    def _check_enabled(self):
        if not self.chatbot.ai_enabled:
            raise ValueError("AI is disabled for this chatbot.")
        if not self.chatbot.is_active:
            raise ValueError("Chatbot is deactivated.")

    def _check_validation(self, message):
        self._check_enabled()
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must not be empty.")
        if len(message) > self.MESSAGE_TEXT_LIMIT:
            raise ValueError(f"message exceeds {self.MESSAGE_TEXT_LIMIT} character limit.")

    def _scoped_user(self, user_id):
        # Default identity is stable across client instances for this conversation.
        return f"{self.chatbot.id}:{user_id if user_id is not None else self.session.id}"

    async def _get_or_create_session(self, scoped_user):
        kwargs = dict(app_name=self.app_name, user_id=scoped_user,
                      session_id=str(self.session.id))
        session = await self.session_service.get_session(**kwargs)
        if session is None:
            session = await self.session_service.create_session(**kwargs)
        return session

    async def _run(self, runner, *, user_id, session_id, message, output_agent_name):
        reply = ""
        usage = TokenUsageSchema()
        source_ids = set()
        appointment = None
        seen_usage_events = set()
        async with aclosing(runner.run_async(
            user_id=user_id, session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=message)]),
            run_config=RunConfig(max_llm_calls=12),
        )) as events:
            async for event in events:
                if event.error_code:
                    raise RuntimeError(f"Agent execution failed: {event.error_code}")
                if event.usage_metadata and not event.partial and event.id not in seen_usage_events:
                    seen_usage_events.add(event.id)
                    metadata = event.usage_metadata
                    prompt = metadata.prompt_token_count or 0
                    output = metadata.candidates_token_count or 0
                    thinking = metadata.thoughts_token_count or 0
                    usage.input_tokens += prompt
                    usage.output_tokens += output + thinking
                    usage.thinking_tokens += thinking
                    usage.cached_input_tokens += metadata.cached_content_token_count or 0
                    usage.total_tokens += metadata.total_token_count or (prompt + output + thinking)
                for response in event.get_function_responses():
                    payload = response.response or {}
                    if response.name == "search_knowledge" and payload.get("status") == "ok":
                        source_ids.update(str(s["source_id"]) for s in payload.get("sources", []))
                    if (response.name == "find_appointment_availability"
                            and payload.get("status") != "search_in_progress"):
                        appointment = AppointmentSchema.model_validate(payload)
                if (event.author == output_agent_name and event.is_final_response()
                        and event.content and not event.partial):
                    text = "".join(p.text for p in event.content.parts or [] if p.text and not p.thought)
                    if text.strip():
                        reply = text.strip()
        return reply, usage, source_ids, appointment

    @staticmethod
    def _response(result, usage):
        cost = (
            usage.input_tokens * settings.GEMINI_INPUT_COST_PER_MILLION
            + usage.output_tokens * settings.GEMINI_OUTPUT_COST_PER_MILLION
        ) / 1_000_000
        return AgentResponseSchema(result=result, token=usage, cost=round(cost, 10)).model_dump(mode="json")

    async def _chat_turn(self, message, user_id, *, confirmation=None):
        scoped_user = self._scoped_user(user_id)
        session = await self._get_or_create_session(scoped_user)
        state_delta = {"temp:booking_confirmation": confirmation}

        if confirmation is not None:
            # The initialization node persists this record before model execution.
            records = dict(session.state.get("booking_confirmations", {}))
            records[confirmation["appointment_id"]] = confirmation
            state_delta["booking_confirmations"] = records

        runner = self._create_runner(
            self.chat_agent, 
            app_name=self.app_name,
            session_service=self.session_service, 
            state_delta=state_delta,
        )

        reply, usage, retrieved_ids, appointment = await self._run(
            runner, 
            user_id=scoped_user, 
            session_id=session.id,
            message=message, 
            output_agent_name=self.chat_agent.name,
        )

        if reply:
            try:
                answer = KnowledgeBaseAgentOutputSchema.model_validate_json(reply)

            except ValidationError as exc:
                raise RuntimeError("Agent returned an invalid structured chat response.") from exc
        else:
            answer = KnowledgeBaseAgentOutputSchema(content=self.chatbot.fallback_message)

        # Reject invented/stale citations; preserve the model's selection of used sources.
        used_ids = list(dict.fromkeys(s for s in answer.source_ids if s in retrieved_ids))

        result = AgentResultSchema(
            content=answer.content, 
            source_ids=used_ids,
            appointment=AppointmentSchema.model_validate(confirmation) if confirmation else appointment,
        )

        return self._response(result, usage)

    async def chat(self, message: str, user_id: str | None = None):
        self._check_validation(message)
        return await self._chat_turn(message, user_id)

    def chat_sync(self, **kwargs):
        return async_to_sync(self.chat)(**kwargs)

    async def confirm_booking(self, appointment_id: str, user_id: str | None = None):
        """
        Backend-only: acknowledge a booking already saved and scoped to this session.
        """
        self._check_enabled()

        confirmation = await sync_to_async(verified_booking)(
            self.chatbot.id, self.session.id, appointment_id,
        )
        return await self._chat_turn(
            json.dumps({"event": "booking_confirmation", "booking": confirmation}),
            user_id, confirmation=confirmation,
        )

    def confirm_booking_sync(self, **kwargs):
        return async_to_sync(self.confirm_booking)(**kwargs)

    def _transcript(self):
        messages = list(self.session.messages.order_by("created_at", "id").values(
            "id", "sender_type", "content", "created_at",
        ))

        if not any(m["sender_type"] == "visitor" for m in messages):
            raise ValueError("At least one visitor message is required for analysis.")

        bookings = conversation_bookings(self.chatbot.id, self.session.id)

        return json.dumps(
            {
                "messages": messages, 
                "backend_bookings": bookings
            }, 
            default=str
        )

    async def generate_lead_summary(self, user_id: str | None = None):
        """Admin-only: analyze the full Django transcript; writes no summary or chat turns."""
        transcript = await sync_to_async(self._transcript)()
        # Admin identity must not select visitor ADK history. Every analysis is isolated.
        service = InMemorySessionService()
        analysis_agent = lead_summary_agent(self.chatbot)
        runner = self._create_runner(
            analysis_agent, 
            app_name="argon_lead_analysis", 
            session_service=service,
        )

        analysis_session = await service.create_session(
            app_name="argon_lead_analysis", 
            user_id=self._scoped_user(user_id),
            session_id=str(uuid4()),
        )

        reply, usage, _, _ = await self._run(
            runner, 
            user_id=analysis_session.user_id, 
            session_id=analysis_session.id,
            message=transcript, 
            output_agent_name=analysis_agent.name,
        )

        try:
            summary = LeadSummaryAgentOutputSchema.model_validate_json(reply)

        except ValidationError as exc:
            raise RuntimeError("Agent returned an invalid lead summary.") from exc

        return self._response(
            AgentResultSchema(
                content=summary.summary, 
                lead_summary=summary
            ), usage
        )

    def generate_lead_summary_sync(self, **kwargs):
        return async_to_sync(self.generate_lead_summary)(**kwargs)



    