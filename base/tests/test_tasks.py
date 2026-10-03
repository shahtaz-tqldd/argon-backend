from datetime import UTC, datetime
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from base.tasks import run_daily_maintenance


@override_settings(TIME_ZONE="UTC", CELERY_TIMEZONE="UTC")
class DailyMaintenanceTaskTests(SimpleTestCase):
    @patch("base.tasks.build_weekly_analytics_snapshot")
    @patch("base.tasks.delete_expired_accounts")
    @patch("base.tasks.reconcile_due_stripe_subscriptions")
    @patch("base.tasks.timezone.now")
    def test_monday_runs_daily_and_weekly_jobs(
        self,
        now,
        reconcile_subscriptions,
        delete_accounts,
        weekly_analytics,
    ):
        now.return_value = datetime(2026, 10, 5, 0, 15, tzinfo=UTC)
        reconcile_subscriptions.return_value = {
            "checked": 1,
            "reconciled": 1,
            "failed": 0,
        }
        delete_accounts.return_value = 2
        weekly_analytics.return_value = {"new_users": 3}

        result = run_daily_maintenance.run()

        self.assertEqual(result["daily"]["expired_accounts_deleted"], 2)
        self.assertEqual(result["weekly"]["analytics"]["new_users"], 3)
        reconcile_subscriptions.assert_called_once_with(now=now.return_value)
        weekly_analytics.assert_called_once_with(now=now.return_value)

    @patch("base.tasks.build_weekly_analytics_snapshot")
    @patch("base.tasks.delete_expired_accounts", return_value=0)
    @patch(
        "base.tasks.reconcile_due_stripe_subscriptions",
        return_value={"checked": 0, "reconciled": 0, "failed": 0},
    )
    @patch("base.tasks.timezone.now")
    def test_non_monday_skips_weekly_jobs(
        self, now, reconcile_subscriptions, delete_accounts, weekly_analytics
    ):
        now.return_value = datetime(2026, 10, 6, 0, 15, tzinfo=UTC)

        result = run_daily_maintenance.run()

        self.assertIsNone(result["weekly"])
        weekly_analytics.assert_not_called()
