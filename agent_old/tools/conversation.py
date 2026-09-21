from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone
from google.adk.tools import FunctionTool

from chat.models import ChatSession
from lead_capture.models import Lead


LEAD_SCORE_SUMMARY_KEY = "lead_score_summary"
MAX_LEAD_SUMMARY_LENGTH = 240
MAX_ESCALATION_REASON_LENGTH = 500


def _clean_text(value, *, field_name, max_length):
    text = " ".join(str(value).split())
    if not text:
        raise ValueError(f"{field_name} must not be empty.")
    if len(text) > max_length:
        raise ValueError(f"{field_name} exceeds {max_length} characters.")
    return text


def _record_lead_score(chatbot_id, session_id, score, summary):
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        raise ValueError("score must be an integer between 0 and 100.")
    summary = _clean_text(
        summary,
        field_name="summary",
        max_length=MAX_LEAD_SUMMARY_LENGTH,
    )

    with transaction.atomic():
        chat_session = ChatSession.objects.select_for_update().get(
            pk=session_id,
            chatbot_id=chatbot_id,
        )
        if chat_session.lead_id is None:
            return {
                "score": score,
                "summary": summary,
                "recorded": False,
                "message": "The visitor has not been captured as a lead yet.",
            }

        lead = Lead.objects.select_for_update().get(
            pk=chat_session.lead_id,
            chatbot_id=chatbot_id,
        )
        lead.lead_score = score
        lead.save(update_fields=["lead_score", "updated_at"])

        metadata = dict(chat_session.metadata or {})
        metadata[LEAD_SCORE_SUMMARY_KEY] = summary
        chat_session.metadata = metadata
        chat_session.save(update_fields=["metadata", "updated_at"])

    return {"score": score, "summary": summary, "recorded": True}


def _request_human_escalation(
    chatbot_id,
    session_id,
    escalation_reason,
):
    escalation_reason = _clean_text(
        escalation_reason,
        field_name="escalation_reason",
        max_length=MAX_ESCALATION_REASON_LENGTH,
    )

    with transaction.atomic():
        chat_session = ChatSession.objects.select_for_update().get(
            pk=session_id,
            chatbot_id=chatbot_id,
        )
        chat_session.requires_attention = True
        chat_session.attention_reason = escalation_reason
        chat_session.attention_requested_at = timezone.now()
        chat_session.save(
            update_fields=[
                "requires_attention",
                "attention_reason",
                "attention_requested_at",
                "updated_at",
            ]
        )

    return {
        "requires_attention": True,
        "escalation_reason": escalation_reason,
    }


def create_conversation_tools(chatbot, session):
    async def record_lead_score(score: int, summary: str) -> dict:
        """Record a qualified lead score and a very short evidence-based reason.

        Call only after the visitor shows a meaningful buying signal, such as a
        concrete need, timeline, budget, booking intent, or requested next step.

        Args:
            score: Qualification score from 0 through 100.
            summary: One short sentence explaining the evidence for the score.
        """
        return await sync_to_async(_record_lead_score, thread_sensitive=True)(
            chatbot.id,
            session.id,
            score,
            summary,
        )

    async def request_human_escalation(
        escalation_reason: str,
    ) -> dict:
        """Request human attention for this conversation.

        Call when the visitor explicitly requests a human, a configured escalation
        rule applies, the assistant cannot safely or confidently help, or a
        required tool fails. Do not merely promise a handoff without calling it.

        Args:
            escalation_reason: A concise dashboard-ready explanation of why a
                human should review the conversation.
        """
        return await sync_to_async(
            _request_human_escalation,
            thread_sensitive=True,
        )(
            chatbot.id,
            session.id,
            escalation_reason,
        )

    return [
        FunctionTool(record_lead_score),
        FunctionTool(request_human_escalation),
    ]
