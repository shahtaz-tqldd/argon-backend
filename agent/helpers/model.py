from functools import lru_cache

from django.conf import settings
from google.adk.models import Gemini
from google.genai import Client, types


@lru_cache(maxsize=1)
def _cached_vertex_client(project, location):
    return Client(vertexai=True, project=project, location=location)


def vertex_client():
    """One shared Vertex client (and HTTP pool) for every agent and request."""
    return _cached_vertex_client(
        settings.GOOGLE_CLOUD_PROJECT_ID,
        settings.GOOGLE_CLOUD_LOCATION,
    )


class ChatGemini(Gemini):
    """Reuse the shared Vertex client instead of building one per agent."""

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
