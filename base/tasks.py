from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from accounts.tasks import delete_expired_accounts
from analytics.services.weekly import build_weekly_analytics_snapshot
from subscription.services.maintenance import reconcile_due_stripe_subscriptions


@shared_task(name="base.tasks.run_daily_maintenance")
def run_daily_maintenance():
    """Single daily coordinator for billing and platform maintenance.

    Add future daily jobs here. Weekly jobs run on Monday in the configured
    Django/Celery timezone.
    """
    now = timezone.now()
    result = {
        "run_at": now.isoformat(),
        "daily": {
            "stripe_subscription_reconciliation": (
                reconcile_due_stripe_subscriptions(now=now)
            ),
            "expired_accounts_deleted": delete_expired_accounts(),
        },
        "weekly": None,
    }
    scheduler_now = timezone.localtime(now, ZoneInfo(settings.CELERY_TIMEZONE))
    if scheduler_now.weekday() == 0:
        result["weekly"] = {
            "analytics": build_weekly_analytics_snapshot(now=now)
        }
    return result
