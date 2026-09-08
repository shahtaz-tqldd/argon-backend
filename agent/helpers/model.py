from functools import cached_property

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from google import genai
from google.adk.models.google_llm import Gemini


class VertexGemini(Gemini):
    """Use the project's Vertex settings without changing process environment."""

    @cached_property
    def api_client(self):
        if not settings.GOOGLE_CLOUD_PROJECT_ID:
            raise ImproperlyConfigured("GOOGLE_CLOUD_PROJECT_ID is required for agents.")
        return genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT_ID,
            location=settings.GOOGLE_CLOUD_LOCATION or "global",
        )


def create_model():
    return VertexGemini(model=settings.GEMINI_CHAT_MODEL)
