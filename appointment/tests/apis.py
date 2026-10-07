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
        self.previous = patched("ChatMessage.objects.filter")
        self.previous.return_value.first.return_value = None
        self.dispatch = patched("dispatch_appointment_reply", return_value=True)
        patched("VisitorAppointmentSerializer").return_value.data = {"id": "appointment"}
        patched("logger")

    def post(self):
        request = APIRequestFactory().post(
            "/?session_id=11111111-1111-1111-1111-111111111111",
            {"starts_at": "2026-10-05T09:00:00+06:00", "collected_fields": {}},
            format="json", HTTP_AUTHORIZATION="Bearer visitor-token",
        )
        return self.view(request, public_key="public-key")

    def test_confirmation_reply_is_queued_after_booking(self):
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.dispatch.assert_called_once_with(
            self.session.id,
            self.appointment.id,
        )
        self.assertTrue(response.data["data"]["reply_queued"])

    def test_queue_failure_does_not_undo_saved_appointment(self):
        self.dispatch.side_effect = RuntimeError("broker offline")
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data["data"]["reply_queued"])

    def test_duplicate_with_existing_reply_is_not_queued_again(self):
        self.book.return_value = self.appointment, False
        self.previous.return_value.first.return_value = SimpleNamespace(content="Original acknowledgment")
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["duplicate"])
        self.assertFalse(response.data["data"]["reply_queued"])
        self.dispatch.assert_not_called()

    def test_unavailable_slot_returns_conflict_without_confirmation(self):
        from django.core.exceptions import ValidationError

        self.book.side_effect = ValidationError("The selected appointment slot is no longer available.")
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.dispatch.assert_not_called()
