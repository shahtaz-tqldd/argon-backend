from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from agent.sub_agents.appointment.tools import find_appointment_slot_counts
from appointment.services.appointment_slot import get_available_appointment_slots


class AppointmentSlotServiceTests(SimpleTestCase):
    def test_schedule_slots_exclude_bookings_and_respect_daily_limit(self):
        day = date(2026, 10, 2)
        schedule = SimpleNamespace(
            weekday=day.weekday(),
            active_slots=[
                SimpleNamespace(start_time=time(9), end_time=time(11)),
            ],
        )
        closed_dates = Mock()
        closed_dates.filter.return_value.exists.return_value = False
        config = SimpleNamespace(
            chatbot_id="bot-id",
            chatbot=SimpleNamespace(timezone="Asia/Dhaka"),
            is_enabled=True,
            maximum_advance_days=30,
            max_appointments_per_day=2,
            appointment_duration_minutes=30,
            closed_dates=closed_dates,
            active_schedules=[schedule],
        )
        existing = SimpleNamespace(
            starts_at=datetime.fromisoformat("2026-10-02T09:30:00+06:00"),
            ends_at=datetime.fromisoformat("2026-10-02T10:00:00+06:00"),
        )
        query = Mock()
        query.only.return_value = [existing]

        with patch(
            "appointment.services.appointment_slot.Appointment.objects.filter",
            return_value=query,
        ):
            slots = get_available_appointment_slots(
                config,
                day,
                now=datetime(2026, 10, 2, 2, tzinfo=timezone.utc),
            )

        # One existing appointment leaves only one daily-capacity unit, even
        # though three non-overlapping schedule slots remain.
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["starts_at"], "2026-10-02T09:00:00+06:00")


class AppointmentAvailabilityResultTests(SimpleTestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            chatbot=SimpleNamespace(timezone="Asia/Dhaka"),
            maximum_advance_days=10,
        )
        self.now = datetime(2026, 10, 2, tzinfo=timezone.utc)

    def test_requested_day_returns_only_its_available_count(self):
        with (
            patch(
                "agent.sub_agents.appointment.tools.get_appointment_booking_config",
                return_value=self.config,
            ),
            patch(
                "agent.sub_agents.appointment.tools.get_available_appointment_slots",
                return_value=[{"starts_at": "one"}, {"starts_at": "two"}],
            ),
            patch(
                "agent.sub_agents.appointment.tools.timezone.now",
                return_value=self.now,
            ),
        ):
            result = find_appointment_slot_counts("bot-id", "2026-10-02")

        self.assertEqual(result, {
            "requested_date": "2026-10-02",
            "is_available_on_requested_date": True,
            "timezone": "UTC+6.00",
            "available_slots": 2,
        })

    def test_unavailable_day_returns_next_three_days_with_slots(self):
        slot_counts = [0, 2, 0, 3, 4]
        side_effect = [[object()] * count for count in slot_counts]
        with (
            patch(
                "agent.sub_agents.appointment.tools.get_appointment_booking_config",
                return_value=self.config,
            ),
            patch(
                "agent.sub_agents.appointment.tools.get_available_appointment_slots",
                side_effect=side_effect,
            ),
            patch(
                "agent.sub_agents.appointment.tools.timezone.now",
                return_value=self.now,
            ),
        ):
            result = find_appointment_slot_counts("bot-id", "2026-10-02")

        self.assertFalse(result["is_available_on_requested_date"])
        self.assertEqual(result["timezone"], "UTC+6.00")
        self.assertEqual(result["requested_date"], "2026-10-02")
        self.assertEqual(result["alternative_dates"], [
            {"date": "2026-10-03", "available_slots": 2},
            {"date": "2026-10-05", "available_slots": 3},
            {"date": "2026-10-06", "available_slots": 4},
        ])
