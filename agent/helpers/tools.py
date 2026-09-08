import json
from app.utils.logger import logger

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from agent.helpers import booking
from vector_store.services.vectorize import KnowledgeVectorService


def create_tools(chatbot, session_id, *, vector_service=None):
    """Bind tenant identity in Python; the model cannot choose a chatbot ID."""
    service = vector_service or KnowledgeVectorService()

    async def search_knowledge(query: str) -> dict:
        """Search this chatbot's knowledge sources for facts relevant to a question."""
        if not chatbot.knowledge_base_enabled:
            return {"status": "disabled", "sources": []}
        try:
            results = await sync_to_async(service.search)(query, chatbot_id=chatbot.id, limit=6)
            return {"status": "ok", "sources": [
                {"source_id": r.knowledge_base_id, "content": r.content} for r in results
            ]}
        except Exception:
            logger.exception("Agent knowledge retrieval failed for chatbot %s", chatbot.id)
            return {"status": "error", "sources": [], "message": "Knowledge search is temporarily unavailable."}

    async def get_booking_fields() -> dict:
        """Get configured required/optional booking fields and the chatbot's timezone."""
        return await sync_to_async(booking.booking_fields)(chatbot.id)

    async def get_chatbot_schedule(requested_date: str) -> dict:
        """Get available appointment slots for a YYYY-MM-DD date in the chatbot timezone."""
        try:
            return await sync_to_async(booking.booking_schedule)(chatbot.id, requested_date)
        except ValueError:
            return {"status": "invalid", "message": "Use a valid YYYY-MM-DD date."}

    async def book_appointment(starts_at: str, collected_fields_json: str, user_confirmed: bool) -> dict:
        """Book a returned ISO slot after the user confirms the slot and collected details.

        collected_fields_json is a JSON object using the configured field values as keys.
        """
        if not user_confirmed:
            return {"status": "confirmation_required"}
        try:
            fields = json.loads(collected_fields_json)
            if not isinstance(fields, dict):
                raise ValueError("Collected fields must be a JSON object.")
            return await sync_to_async(booking.create_booking)(chatbot.id, session_id, starts_at, fields)
        except (ValueError, ValidationError) as exc:
            return {"status": "invalid", "message": str(exc)}
        except booking.AppointmentBookingConfig.DoesNotExist:
            return {"status": "disabled"}
        except IntegrityError:
            return {"status": "unavailable", "message": "Refresh availability before trying again."}

    return search_knowledge, get_booking_fields, get_chatbot_schedule, book_appointment
