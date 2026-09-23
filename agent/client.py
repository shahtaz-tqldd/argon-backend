import json
from contextlib import aclosing

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import ValidationError

from agent.root_agent import root_agent
from agent.sub_agents.appointment.tools import verified_booking
from agent.sub_agents.knowledge.tools import RETRIEVED_SOURCE_IDS_KEY
from agent.utils.schema import (
    AgentResponseSchema,
    AgentResultSchema,
    AppointmentSchema,
    EscalationSchema,
    SpecialistResponseSchema,
    LeadScoreSchema,
    TokenUsageSchema,
)

# global instruction
from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin
from agent.helpers.global_instruction import global_instruction


class AgentClient:
    """Caller authorizes access, persists Django replies, and serializes session turns."""

    app_name = "argon_agents"
    MESSAGE_TEXT_LIMIT = 1000
    MAX_LLM_CALLS = 12
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
        self.specialist_names = {agent.name for agent in self.chat_agent.sub_agents}
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
            plugins=[
                GlobalInstructionPlugin(
                    global_instruction=lambda _ctx: global_instruction(chatbot)
                )
            ]
        )
        self.runner = Runner(app=self.app, session_service=self.session_service)

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
        kwargs = dict(
            app_name=self.app_name,
            user_id=scoped_user,
            session_id=str(self.session.id)
        )

        session = await self.session_service.get_session(**kwargs)

        if session is None:
            session = await self.session_service.create_session(**kwargs)

        return session

    async def _prepare_turn(self, user_id, *, confirmation=None):
        """Load the ADK session and commit trusted backend state before the run."""
        scoped_user = self._scoped_user(user_id)
        session = await self._get_or_create_session(scoped_user)
        state_delta = {"current_booking_confirmation": confirmation}

        if confirmation is not None:
            # Keep backend-verified records even if the subsequent model call fails.
            records = dict(session.state.get("booking_confirmations", {}))
            records[confirmation["appointment_id"]] = confirmation
            state_delta["booking_confirmations"] = records

        # ADK 2.1's App node path does not forward Runner.state_delta to agent
        # context. Append trusted backend state first so instructions and tools
        # see it, while still using the App runner for the actual turn.
        await self.session_service.append_event(
            session,
            Event(
                author=self.chat_agent.name,
                actions=EventActions(state_delta=state_delta),
            ),
        )
        return session

    async def _turn_stream(self, message, user_id, *, confirmation=None, streaming=False):
        """Yield ``{"type": "delta", "content": ...}`` chunks (streaming only)
        and finally ``{"type": "done", "response": <envelope>``."""
        session = await self._prepare_turn(user_id, confirmation=confirmation)

        reply = ""
        usage = TokenUsageSchema()
        retrieved_ids = set()
        cited_ids = []
        appointment = None
        lead_score = None
        escalation = None
        seen_usage_events = set()
        run_config = RunConfig(
            max_llm_calls=self.MAX_LLM_CALLS,
            streaming_mode=StreamingMode.SSE if streaming else StreamingMode.NONE,
        )

        async with aclosing(
            self.runner.run_async(
                user_id=self._scoped_user(user_id),
                session_id=session.id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=message)],
                ),
                run_config=run_config,
            )
        ) as events:
            async for event in events:
                if event.error_code:
                    raise RuntimeError(f"Agent execution failed: {event.error_code}")
                if (
                    event.usage_metadata
                    and not event.partial
                    and event.id not in seen_usage_events
                ):
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

                if (
                    streaming
                    and event.partial
                    and event.author == self.chat_agent.name
                    and event.content
                ):
                    delta = "".join(
                        p.text
                        for p in event.content.parts or []
                        if p.text and not p.thought
                    )
                    if delta:
                        yield {"type": "delta", "content": delta}

                for response in event.get_function_responses():
                    payload = response.response or {}
                    if response.name == "search_knowledge" and payload.get("status") == "ok":
                        retrieved_ids.update(str(s["source_id"]) for s in payload.get("sources", []))
                    if response.name in self.specialist_names:
                        cited_ids.extend(self._cited_source_ids(payload))
                    if (response.name == "find_appointment_availability"
                            and payload.get("status") != "search_in_progress"):
                        appointment = AppointmentSchema.model_validate(payload)
                    if response.name == "record_lead_score":
                        lead_score = LeadScoreSchema.model_validate(payload)
                    if response.name == "request_human_escalation":
                        escalation = EscalationSchema.model_validate(payload)

                if (
                    event.author in self.specialist_names
                    and event.is_final_response()
                    and event.content
                    and not event.partial
                ):
                    text = "".join(p.text or "" for p in event.content.parts or [])
                    cited_ids.extend(self._cited_source_ids(text))

                if (
                    event.author == self.chat_agent.name
                    and event.is_final_response()
                    and event.content
                    and not event.partial
                ):
                    text = "".join(
                        p.text
                        for p in event.content.parts or []
                        if p.text and not p.thought
                    )
                    if text.strip():
                        reply = text.strip()

        # Citations may reference sources retrieved in earlier turns, which the
        # knowledge tool accumulates in session state for exactly this case.
        allowed_ids = retrieved_ids | set(session.state.get(RETRIEVED_SOURCE_IDS_KEY) or [])
        used_ids = list(dict.fromkeys(s for s in cited_ids if s in allowed_ids))

        result = AgentResultSchema(
            content=reply or self.chatbot.fallback_message,
            source_ids=used_ids,
            appointment=(
                AppointmentSchema.model_validate(confirmation)
                if confirmation
                else appointment
            ),
            lead_score=lead_score,
            escalation=escalation,
        )

        yield {"type": "done", "response": self._response(result, usage)}

    def _cited_source_ids(self, payload):
        """Extract source_ids from a specialist's structured answer."""
        if isinstance(payload, types.Content):
            payload = "".join(p.text or "" for p in payload.parts or [])
        if isinstance(payload, list):
            payload = "".join(getattr(p, "text", "") or "" for p in payload)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                return []
        if isinstance(payload, dict) and "result" in payload and "source_ids" not in payload:
            payload = payload.get("result")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    return []
        if isinstance(payload, dict):
            try:
                return SpecialistResponseSchema.model_validate(payload).source_ids
            except ValidationError:
                return []
        return []

    @staticmethod
    def _response(result, usage):
        cost = (
            usage.input_tokens * settings.GEMINI_INPUT_COST_PER_MILLION
            + usage.output_tokens * settings.GEMINI_OUTPUT_COST_PER_MILLION
        ) / 1_000_000
        return AgentResponseSchema(
            result=result,
            token=usage,
            cost=round(cost, 10),
        ).model_dump(mode="json")

    async def _turn(self, message, user_id, *, confirmation=None):
        response = None
        async for item in self._turn_stream(message, user_id, confirmation=confirmation):
            if item["type"] == "done":
                response = item["response"]
        return response

    async def chat(self, message: str, user_id: str | None = None):
        self._check_validation(message)
        return await self._turn(message, user_id)

    def chat_sync(self, **kwargs):
        return async_to_sync(self.chat)(**kwargs)

    async def chat_stream(self, message: str, user_id: str | None = None):
        """Stream one visitor turn for socket-style consumers.

        Yields ``{"type": "delta", "content": str}`` text chunks as the
        coordinator produces them, then a single
        ``{"type": "done", "response": <AgentResponseSchema dump>}`` frame with
        the same envelope ``chat`` returns (content, source_ids, appointment,
        lead_score, escalation, tokens, cost). Consumers should emit an error
        frame if this generator raises mid-stream.
        """
        self._check_validation(message)
        async for item in self._turn_stream(message, user_id, streaming=True):
            yield item

    async def confirm_booking(self, appointment_id: str, user_id: str | None = None):
        """
        Backend-only: acknowledge a booking already saved and scoped to this session.
        """
        self._check_enabled()

        confirmation = await sync_to_async(verified_booking)(
            self.chatbot.id, self.session.id, appointment_id,
        )
        return await self._turn(
            json.dumps({"event": "booking_confirmation", "booking": confirmation}),
            user_id,
            confirmation=confirmation,
        )

    def confirm_booking_sync(self, **kwargs):
        return async_to_sync(self.confirm_booking)(**kwargs)
