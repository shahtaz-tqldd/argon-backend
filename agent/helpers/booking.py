"""Chatbot-scoped availability and transactional appointment creation."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.db import transaction
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


@transaction.atomic
def create_booking(chatbot_id, session_id, starts_at, collected_fields):
    # Serialize agent bookings for a chatbot, then recheck current availability.
    config = AppointmentBookingConfig.objects.select_for_update().select_related("chatbot").get(chatbot_id=chatbot_id)
    if not config.is_enabled:
        return {"status": "disabled"}
    start = datetime.fromisoformat(starts_at)
    if start.tzinfo is None:
        raise ValueError("starts_at must include a timezone offset.")
    existing = Appointment.objects.filter(
        chatbot_id=chatbot_id, starts_at=start, status__in=OPEN_STATUSES,
        metadata__agent_session_id=str(session_id),
    ).first()
    if existing:
        return {"status": "booked", "appointment_id": str(existing.id), "appointment_status": existing.status}
    day = start.astimezone(ZoneInfo(config.chatbot.timezone)).date()
    slot = next((s for s in available_slots(config, day) if datetime.fromisoformat(s["starts_at"]) == start), None)
    if slot is None:
        return {"status": "unavailable", "message": "Refresh the schedule and choose an available slot."}
    appointment = Appointment(
        chatbot_id=chatbot_id, starts_at=start,
        ends_at=datetime.fromisoformat(slot["ends_at"]),
        collected_fields=collected_fields,
        metadata={"agent_session_id": str(session_id)},
    )
    appointment.full_clean()
    appointment.save()
    return {"status": "booked", "appointment_id": str(appointment.id),
            "appointment_status": appointment.status, **slot,
            "message": config.confirmation_message}
