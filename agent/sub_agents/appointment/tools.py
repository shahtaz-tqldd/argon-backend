"""Appointment availability tools and backend booking operations."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async
from django.utils import timezone
from google.adk.tools import FunctionTool, ToolContext

from appointment.models import Appointment, AppointmentBookingConfig


OPEN_STATUSES = ("pending", "confirmed")
_APPOINTMENT_PAYLOADS = ContextVar("appointment_payloads", default=None)


@contextmanager
def appointment_payload_scope():
    """Share offers with the caller across ADK tasks, isolated to this invocation."""
    payloads = {}
    token = _APPOINTMENT_PAYLOADS.set(payloads)
    try:
        yield payloads
    finally:
        _APPOINTMENT_PAYLOADS.reset(token)


def day_availability(config, day, *, now=None):
    """Build visitor-safe slot choices and explain why a day cannot be booked."""
    zone = ZoneInfo(config.chatbot.timezone)
    now = now or timezone.now()
    today = now.astimezone(zone).date()
    base = {"slots": [], "available_count": 0, "remaining_capacity": None}
    if not config.is_enabled:
        return {**base, "reason": "disabled"}
    if not today <= day <= today + timedelta(days=config.maximum_advance_days):
        return {**base, "reason": "outside_booking_horizon"}
    if config.closed_dates.filter(date=day, is_active=True).exists():
        return {**base, "reason": "closed_date"}
    schedules = list(config.schedules.filter(weekday=day.weekday(), is_active=True))
    if not schedules:
        return {**base, "reason": "not_offered"}
    start = datetime.combine(day, datetime.min.time(), zone)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
    # Only intervals are needed; never expose customer data to the model or UI.
    bookings = list(Appointment.objects.filter(
        chatbot_id=config.chatbot_id, status__in=OPEN_STATUSES,
        starts_at__lt=end, ends_at__gt=start,
    ).only("starts_at", "ends_at"))
    remaining = (
        max(0, config.max_appointments_per_day - len(bookings))
        if config.max_appointments_per_day else None
    )
    duration = timedelta(minutes=config.appointment_duration_minutes)
    if duration <= timedelta(0):
        return {**base, "reason": "not_offered"}
    slots = {}
    for schedule in schedules:
        for window in schedule.slots.filter(is_active=True):
            cursor = datetime.combine(day, window.start_time, zone).astimezone(dt_timezone.utc)
            stop = datetime.combine(day, window.end_time, zone).astimezone(dt_timezone.utc)
            while cursor + duration <= stop:
                finish = cursor + duration
                booked = any(b.starts_at < finish and b.ends_at > cursor for b in bookings)
                reason = (
                    "booked" if booked else "past" if cursor <= now
                    else "daily_limit_reached" if remaining == 0 else None
                )
                local_start = cursor.astimezone(zone).isoformat()
                slots[local_start] = {
                    "starts_at": local_start,
                    "ends_at": finish.astimezone(zone).isoformat(),
                    "booked": booked,
                    "available": reason is None,
                    "reason": reason,
                }
                cursor = finish
    choices = sorted(slots.values(), key=lambda slot: slot["starts_at"])
    count = sum(slot["available"] for slot in choices)
    reason = (
        None if count else "daily_limit_reached" if remaining == 0
        else "not_offered" if not choices
        else "fully_booked" if any(slot["booked"] for slot in choices)
        else "no_future_slots"
    )
    return {"slots": choices, "available_count": count,
            "remaining_capacity": remaining, "reason": reason}


def available_slots(config, day, *, now=None):
    """Selectable slots, shared by offers and the locked booking validation."""
    return [slot for slot in day_availability(config, day, now=now)["slots"]
            if slot["available"]]


def get_config(chatbot_id):
    return (
        AppointmentBookingConfig.objects.select_related("chatbot")
        .filter(chatbot_id=chatbot_id, is_enabled=True)
        .first()
    )


def booking_fields(chatbot_id):
    config = get_config(chatbot_id)
    if config is None:
        return {"status": "disabled"}
    return {
        "status": "ok",
        "timezone": config.chatbot.timezone,
        "today": timezone.localdate(
            timezone=ZoneInfo(config.chatbot.timezone)
        ).isoformat(),
        "fields": [
            field
            for field in config.collectable_fields
            if field["mode"] != "hidden"
        ],
        "maximum_advance_days": config.maximum_advance_days,
        "duration_minutes": config.appointment_duration_minutes,
    }


def booking_schedule(chatbot_id, requested_date):
    config = get_config(chatbot_id)
    if config is None:
        return {"status": "disabled"}
    day = date.fromisoformat(requested_date)
    return {
        "status": "ok",
        "timezone": config.chatbot.timezone,
        **day_availability(config, day),
    }


def find_availability(chatbot_id, requested_date):
    """Check the requested day, then find the next date within the booking horizon."""
    config = get_config(chatbot_id)
    if config is None:
        return {"status": "disabled", "available": False}
    zone = ZoneInfo(config.chatbot.timezone)
    now = timezone.now()
    today = now.astimezone(zone).date()
    last_allowed = today + timedelta(days=config.maximum_advance_days)
    try:
        day = date.fromisoformat(requested_date)
        if day.isoformat() != requested_date:
            raise ValueError
    except (TypeError, ValueError):
        return {
            "status": "invalid",
            "available": False,
            "message": "Use a valid YYYY-MM-DD date.",
        }
    base = {
        "available": False,
        "requested_date": day.isoformat(),
        "date": None,
        "timezone": config.chatbot.timezone,
    }
    if not today <= day <= last_allowed:
        return {
            **base,
            "status": "invalid",
            "message": f"Choose a date between {today} and {last_allowed}.",
        }
    end = last_allowed
    requested = day_availability(config, day, now=now)
    base["requested_date_reason"] = requested["reason"]
    cursor = day
    while cursor <= end:
        details = requested if cursor == day else day_availability(config, cursor, now=now)
        if details["available_count"]:
            return {
                **base,
                "status": "available",
                "available": True,
                "date": cursor.isoformat(),
                "searched_through": cursor.isoformat(),
                **details,
            }
        cursor += timedelta(days=1)
    return {
        **base,
        "status": "unavailable",
        "searched_through": end.isoformat(),
        "next_search_date": (
            cursor.isoformat() if cursor <= last_allowed else None
        ),
        "message": (
            "No availability before the booking horizon. Ask for an earlier date."
        ),
    }


def verified_booking(chatbot_id, session_id, appointment_id):
    """Read a saved booking; tenant and conversation identity are never model inputs."""
    appointment = Appointment.objects.filter(
        pk=appointment_id,
        chatbot_id=chatbot_id,
        metadata__chat_session_id=str(session_id),
        status__in=OPEN_STATUSES,
    ).first()
    if appointment is None:
        raise ValueError(
            "No pending or confirmed booking belongs to this conversation."
        )
    return {
        "status": "booking_recorded",
        "available": False,
        "appointment_id": str(appointment.id),
        "appointment_status": appointment.status,
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
    }


def conversation_bookings(chatbot_id, session_id):
    """Read saved appointment outcomes for an admin conversation analysis."""
    return list(
        Appointment.objects.filter(
            chatbot_id=chatbot_id,
            metadata__chat_session_id=str(session_id),
        )
        .order_by("starts_at", "id")
        .values("id", "status", "starts_at", "ends_at")
    )


def create_appointment_tools(chatbot):
    async def find_appointment_availability(
        requested_date: str,
        tool_context: ToolContext,
    ) -> dict:
        """Check a preferred date and suggest the next available day within the booking horizon.

        Args:
            requested_date: Visitor's preferred date as YYYY-MM-DD in the business timezone.
        """
        previous = tool_context.state.get("temp:appointment_search")

        if previous is not None:
            return previous

        if tool_context.state.get("current_booking_confirmation"):
            return {
                "status": "disabled",
                "available": False,
                "message": "Acknowledge the saved booking; no search is needed.",
            }

        tool_context.state["temp:appointment_search"] = {
            "status": "search_in_progress",
            "available": False,
            "message": "An availability search is already in progress this turn.",
        }

        result = await sync_to_async(find_availability)(
            chatbot.id,
            requested_date,
        )
        # Keep slots out of model history and attach them to the caller's reply.
        payloads = _APPOINTMENT_PAYLOADS.get()
        if payloads is not None:
            payloads["appointment"] = result
        summary = {key: value for key, value in result.items() if key != "slots"}
        tool_context.state["temp:appointment_search"] = summary
        return summary

    return [FunctionTool(find_appointment_availability)]
