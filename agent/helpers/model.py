from contextlib import asynccontextmanager
from contextvars import ContextVar

from django.conf import settings
from google.adk.models import Gemini
from google.genai import Client, types


_VERTEX_CLIENT_SCOPE = ContextVar("vertex_client_scope", default=None)


@asynccontextmanager
async def vertex_client_scope():
    """Own one lazy HTTP pool for a turn, and close it on its owning loop."""
    scope = {"client": None}
    token = _VERTEX_CLIENT_SCOPE.set(scope)
    try:
        yield
    finally:
        try:
            client = scope["client"]
            if client is not None:
                try:
                    await client.aio.aclose()
                finally:
                    client.close()
        finally:
            _VERTEX_CLIENT_SCOPE.reset(token)


def vertex_client():
    """Share a client across agents in this turn, never across event loops."""
    scope = _VERTEX_CLIENT_SCOPE.get()
    if scope is None:
        raise RuntimeError("Vertex requests require an active vertex_client_scope.")
    if scope["client"] is None:
        scope["client"] = Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT_ID,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
    return scope["client"]


class ChatGemini(Gemini):
    """Reuse the turn's Vertex client across coordinator and specialists."""

    @property
    def api_client(self):
        return vertex_client()


def chat_model():
    return ChatGemini(model=settings.GEMINI_CHAT_MODEL)


def generation_config():
    return types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=settings.GEMINI_CHAT_MAX_OUTPUT_TOKENS,
    )
