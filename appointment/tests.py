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
