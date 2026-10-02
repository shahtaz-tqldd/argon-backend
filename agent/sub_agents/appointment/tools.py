from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async
from django.utils import timezone
from google.adk.tools import FunctionTool, ToolContext

from appointment.models import Appointment
from appointment.services.appointment_slot import (
    get_appointment_booking_config,
    get_available_appointment_slots,
)
from appointment.utils.choices import AppointmentStatus

def _timezone_offset(timezone_name, day):
    """Render the business UTC offset in the API's compact display format."""
    offset = datetime.combine(day, time(12), ZoneInfo(timezone_name)).utcoffset()
    total_minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours}.{minutes:02d}"


def find_appointment_slot_counts(chatbot_id, requested_date):
    """Return availability count for a requested date or the next three days."""
    config = get_appointment_booking_config(chatbot_id)
    if config is None:
        return {
            "requested_date": requested_date,
            "is_available_on_requested_date": False,
            "timezone": None,
            "error": "Appointment booking is not available.",
        }

    try:
        requested_day = date.fromisoformat(requested_date)
        if requested_day.isoformat() != requested_date:
            raise ValueError
    except (TypeError, ValueError):
        return {
            "requested_date": requested_date,
            "is_available_on_requested_date": False,
            "timezone": _timezone_offset(
                config.chatbot.timezone,
                timezone.localdate(),
            ),
            "error": "Use a valid YYYY-MM-DD date.",
        }

    zone = ZoneInfo(config.chatbot.timezone)
    today = timezone.now().astimezone(zone).date()
    last_bookable_day = today + timedelta(days=config.maximum_advance_days)
    timezone_label = _timezone_offset(config.chatbot.timezone, requested_day)
    if requested_day < today or requested_day > last_bookable_day:
        return {
            "requested_date": requested_date,
            "is_available_on_requested_date": False,
            "timezone": timezone_label,
            "error": f"Choose a date between {today} and {last_bookable_day}.",
        }

    slots = get_available_appointment_slots(config, requested_day)
    if slots:
        return {
            "requested_date": requested_date,
            "is_available_on_requested_date": True,
            "timezone": timezone_label,
            "available_slots": len(slots),
        }

    result = {
        "requested_date": requested_date,
        "is_available_on_requested_date": False,
        "timezone": timezone_label,
        "alternative_dates": [],
    }

    cursor = requested_day + timedelta(days=1)

    while cursor <= last_bookable_day and len(result["alternative_dates"]) < 3:
        slots = get_available_appointment_slots(config, cursor)

        if slots:
            result["alternative_dates"].append({
                "date": cursor.isoformat(),
                "available_slots": len(slots),
            })

        cursor += timedelta(days=1)

    return result


def create_appointment_tools(chatbot):
    async def find_appointment_availability(
        requested_date: str,
        tool_context: ToolContext,
    ) -> dict:
        """Check a date and return availability or the next three available dates.

        Args:
            requested_date: Preferred date in YYYY-MM-DD format.
        """
        return await sync_to_async(find_appointment_slot_counts)(
            chatbot.id,
            requested_date,
        )

    return [FunctionTool(find_appointment_availability)]


def slots_for_agreed_date(chatbot_id, agreed_date):
    """Build the UI payload after the model and visitor agree on a date."""
    config = get_appointment_booking_config(chatbot_id)
    if config is None:
        return None

    day = date.fromisoformat(str(agreed_date))
    slots = get_available_appointment_slots(config, day)
    return {
        "status": "available" if slots else "unavailable",
        "available": bool(slots),
        "requested_date": day.isoformat(),
        "date": day.isoformat(),
        "timezone": _timezone_offset(config.chatbot.timezone, day),
        "available_count": len(slots),
        "slots": slots,
    }


def verified_booking(chatbot_id, session_id, appointment_id):
    """Return a saved booking only when it belongs to this chatbot and session."""
    appointment = Appointment.objects.filter(
        pk=appointment_id,
        chatbot_id=chatbot_id,
        metadata__chat_session_id=str(session_id),
        status__in=(AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED),
    ).first()
    if appointment is None:
        raise ValueError("Appointment was not found for this conversation.")
    return {
        "status": "booking_recorded",
        "available": False,
        "appointment_id": str(appointment.id),
        "appointment_status": appointment.status,
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
    }
