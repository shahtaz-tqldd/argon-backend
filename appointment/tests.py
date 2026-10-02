from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.urls import resolve, reverse
from rest_framework.test import APIRequestFactory

from appointment.api.v1.client.serializers import AppointmentStatsQuerySerializer
from appointment.api.v1.client.views import AppointmentStatsAPIView
from appointment.utils.choices import AppointmentStatus


class AppointmentStatsTests(SimpleTestCase):
    def test_date_validation_accepts_single_day_and_rejects_reversed_range(self):
        for dates, valid in [
            ({"start_date": "2026-10-01", "end_date": "2026-10-01"}, True),
            ({"start_date": "2026-10-01", "end_date": "2026-10-31"}, True),
            ({"start_date": "2026-10-31", "end_date": "2026-10-01"}, False),
            ({"start_date": "invalid"}, False),
            ({}, True),
        ]:
            with self.subTest(dates=dates):
                serializer = AppointmentStatsQuerySerializer(data={"chatbot_slug": "support", **dates})
                self.assertEqual(serializer.is_valid(), valid)

    def stats(self, query):
        view = AppointmentStatsAPIView()
        chatbot = SimpleNamespace(timezone="Asia/Dhaka")
        view.get_chatbot_query = Mock(return_value=query)
        view.get_chatbot = Mock(return_value=chatbot)
        queryset = Mock()
        queryset.filter.return_value = queryset
        counts = {"total": 8, "booked": 3, "confirmed": 2, "cancelled": 1}
        queryset.aggregate.return_value = counts
        with patch("appointment.api.v1.client.views.Appointment.objects.filter", return_value=queryset) as initial:
            response = view.get(Mock())
        initial.assert_called_once_with(chatbot=chatbot)
        self.assertEqual(response.data["data"], counts)
        return queryset

    def test_same_day_includes_whole_day_in_chatbot_timezone(self):
        day = date(2026, 10, 1)
        queryset = self.stats({"start_date": day, "end_date": day})
        boundaries = queryset.filter.call_args_list
        self.assertEqual(boundaries[0].kwargs, {
            "starts_at__gte": datetime.combine(day, time.min, tzinfo=ZoneInfo("Asia/Dhaka")),
        })
        self.assertEqual(boundaries[1].kwargs, {
            "starts_at__lte": datetime.combine(day, time.max, tzinfo=ZoneInfo("Asia/Dhaka")),
        })

    def test_no_dates_returns_all_time_and_aggregates_expected_statuses(self):
        queryset = self.stats({})
        queryset.filter.assert_not_called()
        expressions = queryset.aggregate.call_args.kwargs
        self.assertIsNone(expressions["total"].filter)
        for key, status in [("booked", AppointmentStatus.PENDING), ("confirmed", AppointmentStatus.CONFIRMED), ("cancelled", AppointmentStatus.CANCELLED)]:
            self.assertEqual(expressions[key].filter.children, [("status", status)])

    def test_range_uses_both_supplied_dates(self):
        queryset = self.stats({"start_date": date(2026, 10, 1), "end_date": date(2026, 10, 31)})
        self.assertEqual(queryset.filter.call_args_list[1].kwargs["starts_at__lte"].date(), date(2026, 10, 31))

    def test_route_and_authentication(self):
        url = reverse("appointment-stats")
        self.assertTrue(url.endswith("appointments/stats/"))
        self.assertEqual(resolve(url).func.view_class, AppointmentStatsAPIView)
        response = AppointmentStatsAPIView.as_view()(APIRequestFactory().get(url, {"chatbot_slug": "support"}))
        self.assertIn(response.status_code, (401, 403))


class VisitorBookingConfirmationTests(SimpleTestCase):
    def setUp(self):
        from contextlib import ExitStack
        from appointment.api.v1.public.views import VisitorAppointmentCreateAPIView

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.view = VisitorAppointmentCreateAPIView.as_view()
        self.session = SimpleNamespace(id="session", visitor_id="visitor")
        self.appointment = SimpleNamespace(
            id="appointment", status="pending",
            starts_at=datetime.fromisoformat("2026-10-05T09:00:00+06:00"),
            ends_at=datetime.fromisoformat("2026-10-05T09:30:00+06:00"),
        )
        module = "appointment.api.v1.public.views"
        def patched(name, **kwargs):
            return self.stack.enter_context(patch(f"{module}.{name}", **kwargs))
        patched("get_public_chatbot", return_value=SimpleNamespace(id="bot"))
        patched("require_allowed_widget_origin")
        patched("get_visitor_chat_session", return_value=self.session)
        self.book = patched("book_visitor_appointment", return_value=(self.appointment, True))
        self.client = patched("AgentClient")
        self.client.return_value.confirm_booking_sync.return_value = {
            "result": {"content": "Thank you! Awaiting approval."},
            "token": {}, "cost": 0,
        }
        self.previous = patched("ChatMessage.objects.filter")
        self.previous.return_value.first.return_value = None
        self.save = patched("save_booking_confirmation")
        self.save.return_value = SimpleNamespace(content="Thank you! Awaiting approval.")
        patched("VisitorAppointmentSerializer").return_value.data = {"id": "appointment"}
        patched("serialize_message_event", return_value={"id": "message", "metadata": {"event_type": "appointment_confirmation"}})
        patched("record_ai_usage")
        patched("logger")

    def post(self):
        request = APIRequestFactory().post(
            "/?session_id=11111111-1111-1111-1111-111111111111",
            {"starts_at": "2026-10-05T09:00:00+06:00", "collected_fields": {}},
            format="json", HTTP_AUTHORIZATION="Bearer visitor-token",
        )
        return self.view(request, public_key="public-key")

    def test_confirmation_is_saved_and_returned_with_event_metadata(self):
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.save.assert_called_once_with(
            self.session, self.appointment, content="Thank you! Awaiting approval.",
        )
        self.assertEqual(response.data["data"]["message"]["metadata"]["event_type"], "appointment_confirmation")
        self.assertEqual(response.data["data"]["agent_reply"], self.save.return_value.content)

    def test_model_failure_still_saves_truthful_pending_acknowledgment(self):
        self.client.return_value.confirm_booking_sync.side_effect = RuntimeError("model offline")
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertIn("Thank you", self.save.call_args.kwargs["content"])
        self.assertIn("awaiting approval", self.save.call_args.kwargs["content"])
        self.assertFalse(response.data["data"]["agent_acknowledged"])

    def test_duplicate_reuses_message_without_another_model_call(self):
        self.book.return_value = self.appointment, False
        self.previous.return_value.first.return_value = SimpleNamespace(content="Original acknowledgment")
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["duplicate"])
        self.assertEqual(response.data["data"]["agent_reply"], "Original acknowledgment")
        self.client.assert_not_called()
        self.save.assert_not_called()

    def test_confirmed_fallback_uses_actual_booking_status(self):
        self.appointment.status = "confirmed"
        self.client.return_value.confirm_booking_sync.side_effect = RuntimeError("model offline")
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.save.call_args.kwargs["content"], "Thank you! Your appointment is confirmed.")

    def test_unavailable_slot_returns_conflict_without_confirmation(self):
        from django.core.exceptions import ValidationError

        self.book.side_effect = ValidationError("The selected appointment slot is no longer available.")
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.client.assert_not_called()
        self.save.assert_not_called()
