"""Weekly AI insights generated from a chatbot's visitor messages.

Every Monday the scheduled task walks each active chatbot, gathers the
previous week's visitor messages (AI/agent replies are never sent to the
model), and asks Gemini for a structured analysis — topics visitors cared
about, recurring questions, intents, and improvement areas — persisted as
one LeadAIInsight row per chatbot per week.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Count
from django.utils import timezone
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from analytics.choices import AIUsageType
from analytics.services.ai_usage import record_ai_usage
from chat.models import ChatMessage
from chat.utils.choices import ChatMessageSenderType
from lead_capture.models import LeadAIInsight


# Bounds so one busy chatbot cannot blow up a single prompt.
VISITOR_MESSAGE_LIMIT = 400
MESSAGE_EXCERPT_LENGTH = 500


class InsightTopic(BaseModel):
    topic: str = Field(description="Product, service, or subject visitors asked about.")
    mentions: int = Field(default=1, ge=1, description="Approximate number of messages about it.")
    note: str = Field(default="", description="One short sentence of context.")


class InsightQuestion(BaseModel):
    question: str = Field(description="A recurring visitor question, phrased as asked.")
    times_asked: int = Field(default=1, ge=1)


class InsightIntent(BaseModel):
    intent: str = Field(
        description="Visitor intent, e.g. pricing, demo request, integrations, "
        "support, booking, complaint, churn risk."
    )
    mentions: int = Field(default=1, ge=1)


class WeeklyLeadInsight(BaseModel):
    """Structured analysis contract returned by the model."""

    summary: str = Field(
        description="Three to five sentence recap of the week's visitor activity."
    )
    topics: list[InsightTopic] = Field(default_factory=list)
    frequently_asked_questions: list[InsightQuestion] = Field(default_factory=list)
    common_intents: list[InsightIntent] = Field(default_factory=list)
    areas_of_improvement: list[str] = Field(
        default_factory=list,
        description="Actionable suggestions for the chatbot owner: knowledge "
        "gaps, missing answers, unclear messaging, escalation patterns.",
    )


def last_week_window(*, today=None):
    """Return the (Monday, Sunday) date pair for the week before ``today``."""
    today = today or timezone.localdate()
    week_start = today - timedelta(days=today.weekday() + 7)
    return week_start, week_start + timedelta(days=6)


def collect_visitor_messages(chatbot, *, week_start, week_end):
    """Visitor messages for the window, oldest first, capped for the prompt."""
    window_end = _start_of_day(week_end + timedelta(days=1))
    queryset = (
        ChatMessage.objects.filter(
            chat_session__chatbot=chatbot,
            chat_session__is_test=False,
            sender_type=ChatMessageSenderType.VISITOR,
            created_at__gte=_start_of_day(week_start),
            created_at__lt=window_end,
        )
        .exclude(content="")
        .order_by("created_at", "id")
    )
    messages = []
    for message in queryset.iterator():
        if message.content.strip():
            messages.append(message)
        if len(messages) >= VISITOR_MESSAGE_LIMIT:
            break
    return messages


def visitor_message_stats(chatbot, *, week_start, week_end):
    """Uncapped message and session counts for the window."""
    window_end = _start_of_day(week_end + timedelta(days=1))
    return ChatMessage.objects.filter(
        chat_session__chatbot=chatbot,
        chat_session__is_test=False,
        sender_type=ChatMessageSenderType.VISITOR,
        created_at__gte=_start_of_day(week_start),
        created_at__lt=window_end,
    ).exclude(content="").aggregate(
        visitor_message_count=Count("id"),
        session_count=Count("chat_session", distinct=True),
    )


def build_insight_prompt(chatbot, *, week_start, week_end, messages):
    lines = [
        f"Chatbot: {chatbot.chatbot_name}",
        f"Business: {chatbot.business_name or '-'}",
        f"Week analyzed: {week_start.isoformat()} to {week_end.isoformat()}",
        "",
        "Below are questions and messages sent by visitors (leads) to this "
        "chatbot during that week. Only visitor messages are included; no "
        "chatbot replies. Analyze them and return the structured insight.",
        "",
        "Visitor messages:",
    ]
    for message in messages:
        excerpt = " ".join(message.content.split())[:MESSAGE_EXCERPT_LENGTH]
        timestamp = timezone.localtime(message.created_at).strftime(
            "%Y-%m-%d %H:%M"
        )
        lines.append(f"- [{timestamp}] {excerpt}")
    return "\n".join(lines)


def gemini_client():
    if not settings.GOOGLE_CLOUD_PROJECT_ID:
        raise ImproperlyConfigured(
            "GOOGLE_CLOUD_PROJECT_ID is required for Vertex AI."
        )
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT_ID,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


def _parse_insight(response):
    if getattr(response, "parsed", None) is not None:
        return response.parsed
    text = (response.text or "").strip() if getattr(response, "text", None) else ""
    if text:
        import json

        return WeeklyLeadInsight.model_validate(json.loads(text))
    raise ValueError("Gemini returned no parsable weekly insight.")


def _token_usage(response):
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}
    return {
        "input_tokens": usage.prompt_token_count or 0,
        "output_tokens": usage.candidates_token_count or 0,
        "thinking_tokens": usage.thoughts_token_count or 0,
        "cached_input_tokens": usage.cached_content_token_count or 0,
        "total_tokens": usage.total_token_count or 0,
    }


def _record_usage(chatbot, response):
    token_usage = _token_usage(response)
    if not token_usage.get("total_tokens"):
        return
    cost = (
        token_usage["input_tokens"] * settings.GEMINI_INPUT_COST_PER_MILLION
        + token_usage["output_tokens"] * settings.GEMINI_OUTPUT_COST_PER_MILLION
    ) / 1_000_000
    record_ai_usage(
        chatbot=chatbot,
        usage_type=AIUsageType.CONTENT_GENERATION,
        cost=round(cost, 10),
        token_usage=token_usage,
        model=settings.GEMINI_CHAT_MODEL,
        metadata={"event": "weekly_lead_insight"},
    )


def generate_weekly_lead_insight(
    chatbot,
    *,
    week_start,
    week_end,
    client=None,
):
    """Generate and persist the weekly insight for one chatbot.

    Returns the LeadAIInsight, or None when the chatbot had no visitor
    messages in the window.
    """
    messages = collect_visitor_messages(
        chatbot,
        week_start=week_start,
        week_end=week_end,
    )
    if not messages:
        return None

    stats = visitor_message_stats(
        chatbot,
        week_start=week_start,
        week_end=week_end,
    )
    prompt = build_insight_prompt(
        chatbot,
        week_start=week_start,
        week_end=week_end,
        messages=messages,
    )
    client = client or gemini_client()
    response = client.models.generate_content(
        model=settings.GEMINI_CHAT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=settings.GEMINI_INSIGHTS_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            response_schema=WeeklyLeadInsight,
        ),
    )
    insight_data = _parse_insight(response)

    insight, _created = LeadAIInsight.objects.update_or_create(
        chatbot=chatbot,
        week_start=week_start,
        defaults={
            "week_end": week_end,
            "session_count": stats["session_count"],
            "visitor_message_count": stats["visitor_message_count"],
            "summary": insight_data.summary,
            "topics": [
                item.model_dump(mode="json") for item in insight_data.topics
            ],
            "frequently_asked_questions": [
                item.model_dump(mode="json")
                for item in insight_data.frequently_asked_questions
            ],
            "common_intents": [
                item.model_dump(mode="json")
                for item in insight_data.common_intents
            ],
            "areas_of_improvement": list(insight_data.areas_of_improvement),
            "metadata": {
                "model": settings.GEMINI_CHAT_MODEL,
                "analyzed_message_count": len(messages),
                "message_limit": VISITOR_MESSAGE_LIMIT,
                "excerpt_length": MESSAGE_EXCERPT_LENGTH,
                "token_usage": _token_usage(response),
            },
        },
    )
    _record_usage(chatbot, response)
    return insight


def _start_of_day(value):
    return timezone.make_aware(
        datetime.combine(value, time.min),
        timezone.get_current_timezone(),
    )
