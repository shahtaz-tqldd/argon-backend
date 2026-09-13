import json
from contextlib import aclosing

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.agents.run_config import RunConfig
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import ValidationError

from agent.root_agent import root_agent
from agent.sub_agents.appointment.tools.booking import verified_booking
from agent.utils.schema import (
    AgentResponseSchema,
    AgentResultSchema,
    AppointmentSchema,
    EscalationSchema,
    KnowledgeBaseAgentOutputSchema,
    LeadScoreSchema,
    TokenUsageSchema,
)


class AgentClient:
    """Caller authorizes access, persists Django replies, and serializes session turns."""

    app_name = "argon_agents"
    MESSAGE_TEXT_LIMIT = 1000
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

    async def _run(
        self,
        *,
        user_id,
        session_id,
        message,
    ):
        reply = ""
        usage = TokenUsageSchema()
        source_ids = set()
        appointment = None
        lead_score = None
        escalation = None
        seen_usage_events = set()
        async with aclosing(
            self.runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=message)],
                ),
                run_config=RunConfig(max_llm_calls=12),
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
                for response in event.get_function_responses():
                    payload = response.response or {}
                    if response.name == "search_knowledge" and payload.get("status") == "ok":
                        source_ids.update(str(s["source_id"]) for s in payload.get("sources", []))
                    if (response.name == "find_appointment_availability"
                            and payload.get("status") != "search_in_progress"):
                        appointment = AppointmentSchema.model_validate(payload)
                    if response.name == "record_lead_score":
                        lead_score = LeadScoreSchema.model_validate(payload)
                    if response.name == "request_human_escalation":
                        escalation = EscalationSchema.model_validate(payload)
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
        return reply, usage, source_ids, appointment, lead_score, escalation

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

    async def _chat_turn(self, message, user_id, *, confirmation=None):
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

        (
            reply,
            usage,
            retrieved_ids,
            appointment,
            lead_score,
            escalation,
        ) = await self._run(
            user_id=scoped_user,
            session_id=session.id,
            message=message,
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
            appointment=(
                AppointmentSchema.model_validate(confirmation)
                if confirmation
                else appointment
            ),
            lead_score=lead_score,
            escalation=escalation,
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
            user_id,
            confirmation=confirmation,
        )

    def confirm_booking_sync(self, **kwargs):
        return async_to_sync(self.confirm_booking)(**kwargs)
