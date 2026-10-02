"""Queries and calculations for bookable appointment slots."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.db.models import Prefetch
from django.utils import timezone

from appointment.models import (
    Appointment,
    AppointmentBookingConfig,
    AppointmentBookingSchedule,
    AppointmentBookingScheduleSlot,
)
from appointment.utils.choices import AppointmentStatus


OPEN_APPOINTMENT_STATUSES = (
    AppointmentStatus.PENDING,
    AppointmentStatus.CONFIRMED,
)


def get_appointment_booking_config(chatbot_id):
    """Load an enabled booking config and its active weekly schedule."""
    active_slots = AppointmentBookingScheduleSlot.objects.filter(
        is_active=True,
    ).order_by("start_time")
    active_schedules = AppointmentBookingSchedule.objects.filter(
        is_active=True,
    ).prefetch_related(
        Prefetch("slots", queryset=active_slots, to_attr="active_slots")
    )
    return (
        AppointmentBookingConfig.objects.select_related("chatbot")
        .prefetch_related(
            Prefetch(
                "schedules",
                queryset=active_schedules,
                to_attr="active_schedules",
            )
        )
        .filter(chatbot_id=chatbot_id, is_enabled=True)
        .first()
    )


def get_available_appointment_slots(config, day: date, *, now=None):
    """Return the unbooked start/end slots for ``day`` under ``config``.

    Schedule windows are divided by ``appointment_duration_minutes``. Pending
    and confirmed appointments consume both their overlapping time and one unit
    of ``max_appointments_per_day`` capacity.
    """
    if not config.is_enabled:
        return []

    zone = ZoneInfo(config.chatbot.timezone)
    now = now or timezone.now()
    today = now.astimezone(zone).date()
    last_bookable_day = today + timedelta(days=config.maximum_advance_days)
    if day < today or day > last_bookable_day:
        return []
    if config.closed_dates.filter(date=day, is_active=True).exists():
        return []

    schedules = getattr(config, "active_schedules", None)
    if schedules is None:
        schedules = config.schedules.filter(
            weekday=day.weekday(),
            is_active=True,
        ).prefetch_related(
            Prefetch(
                "slots",
                queryset=AppointmentBookingScheduleSlot.objects.filter(
                    is_active=True,
                ).order_by("start_time"),
                to_attr="active_slots",
            )
        )
    else:
        schedules = [
            schedule
            for schedule in schedules
            if schedule.weekday == day.weekday()
        ]

    day_start = datetime.combine(day, datetime.min.time(), zone)
    day_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
    appointments = list(
        Appointment.objects.filter(
            chatbot_id=config.chatbot_id,
            status__in=OPEN_APPOINTMENT_STATUSES,
            starts_at__lt=day_end,
            ends_at__gt=day_start,
        ).only("starts_at", "ends_at")
    )

    remaining_capacity = None
    if config.max_appointments_per_day is not None:
        remaining_capacity = max(
            0,
            config.max_appointments_per_day - len(appointments),
        )
        if remaining_capacity == 0:
            return []

    duration = timedelta(minutes=config.appointment_duration_minutes)
    if duration <= timedelta(0):
        return []

    available = {}
    for schedule in schedules:
        slots = getattr(schedule, "active_slots", None)
        if slots is None:
            slots = schedule.slots.filter(is_active=True).order_by("start_time")
        for window in slots:
            cursor = datetime.combine(
                day,
                window.start_time,
                zone,
            ).astimezone(dt_timezone.utc)
            window_end = datetime.combine(
                day,
                window.end_time,
                zone,
            ).astimezone(dt_timezone.utc)
            while cursor + duration <= window_end:
                slot_end = cursor + duration
                overlaps = any(
                    appointment.starts_at < slot_end
                    and appointment.ends_at > cursor
                    for appointment in appointments
                )
                if cursor > now and not overlaps:
                    local_start = cursor.astimezone(zone)
                    available[local_start.isoformat()] = {
                        "starts_at": local_start.isoformat(),
                        "ends_at": slot_end.astimezone(zone).isoformat(),
                    }
                cursor = slot_end

    result = [available[key] for key in sorted(available)]
    if remaining_capacity is not None:
        result = result[:remaining_capacity]
    return result
