"""Chatbot-scoped availability and verification of bookings saved by the UI."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone

from appointment_booking.models import Appointment, AppointmentBookingConfig


OPEN_STATUSES = ("pending", "confirmed")


def available_slots(config, day, *, now=None):
    zone = ZoneInfo(config.chatbot.timezone)
    now = now or timezone.now()
    today = now.astimezone(zone).date()
    if not config.is_enabled or not today <= day <= today + timedelta(days=config.maximum_advance_days):
        return []
    if config.closed_dates.filter(date=day, is_active=True).exists():
        return []
    start = datetime.combine(day, datetime.min.time(), zone)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
    bookings = list(Appointment.objects.filter(
        chatbot_id=config.chatbot_id, status__in=OPEN_STATUSES,
        starts_at__lt=end, ends_at__gt=start,
    ))
    if config.max_appointments_per_day and len(bookings) >= config.max_appointments_per_day:
        return []
    duration = timedelta(minutes=config.appointment_duration_minutes)
    if duration <= timedelta(0):
        return []
    slots = {}
    for schedule in config.schedules.filter(weekday=day.weekday(), is_active=True):
        for window in schedule.slots.filter(is_active=True):
            cursor = datetime.combine(day, window.start_time, zone).astimezone(dt_timezone.utc)
            stop = datetime.combine(day, window.end_time, zone).astimezone(dt_timezone.utc)
            while cursor + duration <= stop:
                finish = cursor + duration
                if cursor > now and not any(b.starts_at < finish and b.ends_at > cursor for b in bookings):
                    local_start = cursor.astimezone(zone).isoformat()
                    slots[local_start] = {"starts_at": local_start, "ends_at": finish.astimezone(zone).isoformat()}
                cursor = finish
    return sorted(slots.values(), key=lambda slot: slot["starts_at"])


def get_config(chatbot_id):
    return AppointmentBookingConfig.objects.select_related("chatbot").filter(chatbot_id=chatbot_id, is_enabled=True).first()


def booking_fields(chatbot_id):
    config = get_config(chatbot_id)
    if config is None:
        return {"status": "disabled"}
    return {
        "status": "ok", "timezone": config.chatbot.timezone,
        "today": timezone.localdate(timezone=ZoneInfo(config.chatbot.timezone)).isoformat(),
        "fields": [f for f in config.collectable_fields if f["mode"] != "hidden"],
        "maximum_advance_days": config.maximum_advance_days,
        "duration_minutes": config.appointment_duration_minutes,
    }


def booking_schedule(chatbot_id, requested_date):
    config = get_config(chatbot_id)
    if config is None:
        return {"status": "disabled"}
    day = date.fromisoformat(requested_date)
    return {"status": "ok", "timezone": config.chatbot.timezone, "slots": available_slots(config, day)}


def find_availability(chatbot_id, requested_date):
    """Check an inclusive seven-day window, stopping at the first available day."""
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
        return {"status": "invalid", "available": False,
                "message": "Use a valid YYYY-MM-DD date."}
    base = {"available": False, "requested_date": day.isoformat(),
            "date": None, "timezone": config.chatbot.timezone}
    if not today <= day <= last_allowed:
        return {**base, "status": "invalid",
                "message": f"Choose a date between {today} and {last_allowed}."}
    end = min(day + timedelta(days=6), last_allowed)
    cursor = day
    while cursor <= end:
        slots = available_slots(config, cursor, now=now)
        if slots:
            return {**base, "status": "available", "available": True,
                    "date": cursor.isoformat(), "searched_through": cursor.isoformat(),
                    "slots": slots}
        cursor += timedelta(days=1)
    return {**base, "status": "unavailable", "searched_through": end.isoformat(),
            "next_search_date": cursor.isoformat() if cursor <= last_allowed else None,
            "message": ("No availability in this seven-day window. Ask about next week."
                        if end == day + timedelta(days=6) and cursor <= last_allowed
                        else "No availability before the booking horizon. Ask for an earlier date.")}


def verified_booking(chatbot_id, session_id, appointment_id):
    """Read a saved booking; tenant and conversation identity are never model inputs."""
    appointment = Appointment.objects.filter(
        pk=appointment_id, chatbot_id=chatbot_id,
        metadata__chat_session_id=str(session_id), status__in=OPEN_STATUSES,
    ).first()
    if appointment is None:
        raise ValueError("No pending or confirmed booking belongs to this conversation.")
    return {
        "status": "booking_recorded", "available": False,
        "appointment_id": str(appointment.id),
        "appointment_status": appointment.status,
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
    }


def conversation_bookings(chatbot_id, session_id):
    """Read saved appointment outcomes for an admin conversation analysis."""
    return list(Appointment.objects.filter(
        chatbot_id=chatbot_id, metadata__chat_session_id=str(session_id),
    ).order_by("starts_at", "id").values("id", "status", "starts_at", "ends_at"))
