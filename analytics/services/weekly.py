from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from accounts.models import User
from analytics.models import AIUsage
from chat.models import ChatSession
from chatbot.models import Chatbot
from subscription.utils.choices import PaymentStatus
from subscription.models import Payment
from workspace.models import Workspace


def build_weekly_analytics_snapshot(*, now=None):
    """Build a JSON-safe seven-day platform snapshot for the task result."""
    now = now or timezone.now()
    start = now - timedelta(days=7)
    ai_totals = AIUsage.objects.filter(created_at__gte=start).aggregate(
        requests=Count("id"),
        tokens=Sum("tokens", default=0),
        cost=Sum("cost", default=0),
    )
    payment_totals = list(
        Payment.objects.filter(
            status=PaymentStatus.SUCCEEDED,
            paid_at__gte=start,
        )
        .values("currency")
        .annotate(count=Count("id"), amount=Sum("amount", default=0))
        .order_by("currency")
    )
    return {
        "period_start": start.isoformat(),
        "period_end": now.isoformat(),
        "new_users": User.objects.filter(created_at__gte=start).count(),
        "new_workspaces": Workspace.objects.filter(created_at__gte=start).count(),
        "new_chatbots": Chatbot.objects.filter(created_at__gte=start).count(),
        "chat_sessions": ChatSession.objects.filter(
            created_at__gte=start, is_test=False
        ).count(),
        "ai_usage": {
            "requests": ai_totals["requests"],
            "tokens": ai_totals["tokens"],
            "cost": str(ai_totals["cost"]),
        },
        "payments": [
            {
                "currency": item["currency"],
                "count": item["count"],
                "amount": str(item["amount"]),
            }
            for item in payment_totals
        ],
    }
