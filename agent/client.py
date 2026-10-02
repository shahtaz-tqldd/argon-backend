import json
from contextlib import aclosing
from datetime import datetime
from uuid import UUID

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.agents.run_config import RunConfig
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import ValidationError

from app.utils.logger import logger
from agent.root_agent import root_agent
from agent.helpers.model import vertex_client_scope
from agent.sub_agents.appointment.context import booking_confirmation_scope
from agent.sub_agents.appointment.tools import (
    slots_for_agreed_date,
    verified_booking,
)
from agent.sub_agents.knowledge.tools import RETRIEVED_SOURCE_IDS_KEY
from analytics.choices import AIUsageType
from analytics.services.ai_usage import record_ai_usage
from chat.models import ChatMessage, ChatSession
from chat.utils.choices import ChatMessageSenderType, ChatSessionStatus
from knowledge.models import KnowledgeBase
from knowledge.services.storage import PrivateKnowledgeStorage
from knowledge.utils.choices import KnowledgeSourceTypes
from agent.schema import (
    AgentResponseSchema,
    AgentResultSchema,
    AppointmentAgentResponseSchema,
    EscalationSchema,
    SpecialistResponseSchema,
    LeadScoreSchema,
    TokenUsageSchema,
)

# global instruction
from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin
from agent.helpers.global_instruction import global_instruction


class AgentClient:
    """Run chat lifecycle events and own reply metadata and persistence."""

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
        # GlobalInstructionPlugin executes its callback from ADK's async model
        # pipeline. Build the instruction here, while still on Django's sync
        # request path, because feature checks may load related ORM objects.
        rendered_global_instruction = global_instruction(chatbot)
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
                    global_instruction=lambda _ctx: rendered_global_instruction
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

    async def _run_turn(self, message, user_id, *, confirmation=None):
        """Run one non-streaming turn; Runner records its message and events."""
        scoped_user = self._scoped_user(user_id)
        session = await self._get_or_create_session(scoped_user)

        reply = ""
        usage = TokenUsageSchema()
        retrieved_ids = set()
        cited_ids = []
        agreed_date = None
        lead_score = None
        escalation = None
        seen_usage_events = set()
        run_config = RunConfig(
            max_llm_calls=self.MAX_LLM_CALLS,
        )

        with booking_confirmation_scope(confirmation):
            async with vertex_client_scope(), aclosing(
                self.runner.run_async(
                    user_id=scoped_user,
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

                    for response in event.get_function_responses():
                        payload = response.response or {}
                        if response.name == "search_knowledge" and payload.get("status") == "ok":
                            retrieved_ids.update(str(s["source_id"]) for s in payload.get("sources", []))
                        if response.name in self.specialist_names:
                            cited_ids.extend(self._cited_source_ids(payload))
                        if response.name == "appointment_agent":
                            specialist_result = self._appointment_result(payload)
                            if specialist_result and specialist_result.agreed_date:
                                agreed_date = specialist_result.agreed_date
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
            agreed_date=agreed_date,
            lead_score=lead_score,
            escalation=escalation,
        )

        return self._response(result, usage)

    @staticmethod
    def _appointment_result(payload):
        """Parse the appointment specialist's structured handoff."""
        if isinstance(payload, types.Content):
            payload = "".join(p.text or "" for p in payload.parts or [])
        if isinstance(payload, list):
            payload = "".join(getattr(p, "text", "") or "" for p in payload)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                return None
        if isinstance(payload, dict) and "result" in payload and "content" not in payload:
            payload = payload.get("result")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    return None
        if not isinstance(payload, dict):
            return None
        try:
            return AppointmentAgentResponseSchema.model_validate(payload)
        except ValidationError:
            return None

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

    @staticmethod
    def _format_slot_time(value):
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return parsed.strftime("%I.%M %p").lstrip("0")

    def _source_metadata(self, source_ids):
        valid_source_ids = []
        for source_id in source_ids:
            try:
                valid_source_ids.append(UUID(str(source_id)))
            except (TypeError, ValueError):
                continue
        sources = KnowledgeBase.objects.filter(
            chatbot_id=self.chatbot.id,
            id__in=valid_source_ids,
        ).only(
            "id",
            "title",
            "source_type",
            "url",
            "original_filename",
            "text_content",
            "file_key",
        )
        sources_by_id = {str(source.id): source for source in sources}
        result = []
        storage = None
        for source_id in source_ids:
            source = sources_by_id.get(str(source_id))
            if source is None:
                continue
            url = source.url
            if source.source_type == KnowledgeSourceTypes.FILE and source.file_key:
                try:
                    storage = storage or PrivateKnowledgeStorage()
                    url = storage.private_url(source.file_key)
                except Exception:
                    logger.exception(
                        "Could not create a URL for knowledge source %s",
                        source.id,
                    )
                    url = None
            result.append({
                "id": str(source.id),
                "source_type": source.source_type,
                "name": source.name,
                "url": url,
            })
        return result

    def _public_reply(self, response):
        """Convert the internal agent envelope to visitor message fields."""
        result = response["result"]
        metadata = {}

        agreed_date = result.get("agreed_date")
        appointment = (
            slots_for_agreed_date(self.chatbot.id, agreed_date)
            if agreed_date
            else None
        )
        if appointment:
            metadata["appointments"] = {
                "date": appointment["date"],
                "timezone": appointment.get("timezone"),
                "available_slots": [
                    {
                        "start_time": self._format_slot_time(slot["starts_at"]),
                        "end_time": self._format_slot_time(slot["ends_at"]),
                    }
                    for slot in appointment.get("slots", [])
                ],
            }

        lead_score = result.get("lead_score")
        if lead_score:
            metadata["lead_analytics"] = {
                "score": lead_score["score"],
                "summary": lead_score["summary"],
            }

        escalation = result.get("escalation")
        if escalation:
            metadata["escalation"] = {
                "reason": escalation["escalation_reason"],
            }

        source_ids = result.get("source_ids") or []
        sources = self._source_metadata(source_ids)
        if sources:
            metadata["sources"] = sources

        return {"content": result["content"], "metadata": metadata}

    def _existing_reply(self, external_id):
        message = ChatMessage.objects.filter(
            chat_session_id=self.session.id,
            external_id=external_id,
        ).first()
        if message is None:
            return None
        return {"content": message.content, "metadata": message.metadata}

    @transaction.atomic
    def _persist_reply(self, external_id, response, *, require_live_ai=True):
        """Persist one generated message and its usage as one transaction."""
        session = ChatSession.objects.select_for_update().get(
            pk=self.session.id,
            chatbot_id=self.chatbot.id,
        )
        existing = ChatMessage.objects.filter(
            chat_session=session,
            external_id=external_id,
        ).first()
        if existing is not None:
            return {"content": existing.content, "metadata": existing.metadata}

        if session.is_test or session.status != ChatSessionStatus.OPEN:
            return None
        if require_live_ai and (
            session.assigned_to_id
            or not session.ai_enabled
            or not self.chatbot.ai_enabled
            or not self.chatbot.is_active
        ):
            return None

        public_reply = self._public_reply(response)
        message = ChatMessage(
            chat_session=session,
            sender_type=ChatMessageSenderType.AI,
            content=public_reply["content"],
            metadata=public_reply["metadata"],
            external_id=external_id,
        )
        message.full_clean()
        message.save()
        if response["token"].get("total_tokens", 0):
            record_ai_usage(
                chatbot=self.chatbot,
                chat_session=session,
                chat_message=message,
                usage_type=AIUsageType.CHAT,
                cost=response["cost"],
                token_usage=response["token"],
                model=settings.GEMINI_CHAT_MODEL,
                metadata=response.get("usage_metadata"),
            )
        return public_reply

    async def _generate_reply(self, visitor_message, user_id: str | None = None):
        """Generate and persist a live reply, returning only public message data."""
        if (
            str(visitor_message.chat_session_id) != str(self.session.id)
            or visitor_message.sender_type != ChatMessageSenderType.VISITOR
        ):
            raise ValueError("The visitor message does not belong to this session.")

        existing = await sync_to_async(
            self._existing_reply,
            thread_sensitive=True,
        )(f"ai:{visitor_message.id}")
        if existing is not None:
            return existing

        self._check_validation(visitor_message.content)
        response = await self._run_turn(visitor_message.content, user_id)
        return await sync_to_async(
            self._persist_reply,
            thread_sensitive=True,
        )(f"ai:{visitor_message.id}", response)

    def generate_reply_sync(self, **kwargs):
        return async_to_sync(self._generate_reply)(**kwargs)

    async def _generate_booking_reply(
        self,
        appointment_id: str,
        user_id: str | None = None,
    ):
        """Generate and persist the next chat message for a saved appointment."""
        external_id = f"appointment:{appointment_id}"
        existing = await sync_to_async(
            self._existing_reply,
            thread_sensitive=True,
        )(external_id)
        if existing is not None:
            return existing

        confirmation = await sync_to_async(verified_booking)(
            self.chatbot.id,
            self.session.id,
            appointment_id,
        )
        response = None
        if self.chatbot.ai_enabled and self.chatbot.is_active:
            try:
                response = await self._run_turn(
                    json.dumps({
                        "event": "booking_confirmation",
                        "booking": confirmation,
                    }),
                    user_id,
                    confirmation=confirmation,
                )
                response["usage_metadata"] = {
                    "event": "appointment_confirmation",
                    "appointment_id": str(appointment_id),
                }
            except Exception:
                logger.exception(
                    "Could not generate confirmation for appointment %s",
                    appointment_id,
                )

        if response is None:
            content = (
                "Thank you! Your appointment is confirmed."
                if confirmation["appointment_status"] == "confirmed"
                else (
                    "Thank you! Your appointment request has been received "
                    "and is awaiting approval."
                )
            )
            response = {
                "result": {
                    "content": content,
                    "source_ids": [],
                    "agreed_date": None,
                    "lead_score": None,
                    "escalation": None,
                },
                "token": {},
                "cost": 0,
            }

        return await sync_to_async(
            self._persist_reply,
            thread_sensitive=True,
        )(external_id, response, require_live_ai=False)

    def generate_booking_reply_sync(self, **kwargs):
        return async_to_sync(self._generate_booking_reply)(**kwargs)

    async def _generate_test_reply(self, message: str, user_id: str | None = None):
        """Generate an unpersisted public reply for the test-chat workflow."""
        if not self.session.is_test:
            raise ValueError("Test replies require a test chat session.")
        self._check_validation(message)
        response = await self._run_turn(message, user_id)
        return await sync_to_async(
            self._public_reply,
            thread_sensitive=True,
        )(response)

    def generate_test_reply_sync(self, **kwargs):
        return async_to_sync(self._generate_test_reply)(**kwargs)
