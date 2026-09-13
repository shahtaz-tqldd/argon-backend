from functools import cached_property

from django.conf import settings
from google.adk.models import Gemini
from google.genai import Client, types


class ChatGemini(Gemini):
    """Use the same explicit Vertex configuration as the existing chat service."""

    @cached_property
    def api_client(self):
        return Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT_ID,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )


def chat_model():
    return ChatGemini(model=settings.GEMINI_CHAT_MODEL)


def generation_config():
    return types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=settings.GEMINI_CHAT_MAX_OUTPUT_TOKENS,
    )
