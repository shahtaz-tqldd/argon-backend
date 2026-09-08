"""Application entry point for Google ADK conversations."""
from asgiref.sync import async_to_sync
from django.conf import settings
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
from google.genai import types

from agent.root_agent import create_root_agent


class AgentClient:
    """Reuse a client across turns. Callers must authorize the chatbot and user IDs.

    ADK_DB_URL enables persistent history. Without it history lasts only for this
    client instance. Serialize concurrent calls for the same session in the caller.
    """
    app_name = "argon_agents"

    def __init__(self, *, session_service=None, vector_service=None):
        self.session_service = session_service if session_service is not None else (
            DatabaseSessionService(db_url=settings.ADK_DB_URL)
            if settings.ADK_DB_URL else InMemorySessionService()
        )
        self.vector_service = vector_service

    async def chat(self, *, chatbot, user_id, session_id, message):
        if not message or not message.strip():
            raise ValueError("message must not be empty.")
        if not user_id or not session_id:
            raise ValueError("user_id and session_id are required.")
        if not chatbot.ai_enabled or not chatbot.is_active:
            raise ValueError("AI is disabled for this chatbot.")
        # Tenant namespace prevents collisions for identically named visitors.
        scoped_user = f"{chatbot.id}:{user_id}"
        session_id = str(session_id)
        session = await self.session_service.get_session(
            app_name=self.app_name, user_id=scoped_user, session_id=session_id,
        )
        if session is None:
            await self.session_service.create_session(
                app_name=self.app_name, user_id=scoped_user, session_id=session_id,
            )
        root = create_root_agent(chatbot, session_id)
        runner = Runner(agent=root, app_name=self.app_name, session_service=self.session_service)
        reply = ""
        async for event in runner.run_async(
            user_id=scoped_user, session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=message)]),
        ):
            if event.error_code:
                raise RuntimeError(f"Agent execution failed: {event.error_code}")
            if event.is_final_response() and event.content:
                text = "".join(p.text for p in event.content.parts or [] if p.text and not p.thought)
                if text.strip():
                    reply = text.strip()
        return reply or chatbot.fallback_message

    def chat_sync(self, **kwargs):
        """Synchronous entry point; use chat() from async code."""
        return async_to_sync(self.chat)(**kwargs)
