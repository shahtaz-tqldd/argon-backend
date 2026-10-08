from django.db.models import Q
from django.utils import timezone

from app.utils.logger import logger
from subscription.utils.choices import (
    PaymentProvider,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription
from subscription.services.stripe import StripeServiceError
from subscription.services.webhooks import StripeWebhookProcessor


RECONCILABLE_STATUSES = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
    SubscriptionStatus.UNPAID,
)


def reconcile_due_stripe_subscriptions(*, now=None):
    """Repair local state for Stripe renewals that are due or incomplete.

    Stripe performs the charge. This job only retrieves authoritative Stripe
    state and applies the subscription/invoice snapshots locally.
    """
    now = now or timezone.now()
    subscriptions = ChatbotSubscription.objects.filter(
        provider=PaymentProvider.STRIPE,
        renewal_mode=RenewalMode.PROVIDER_MANAGED,
        status__in=RECONCILABLE_STATUSES,
    ).exclude(provider_subscription_id__isnull=True).exclude(
        provider_subscription_id=""
    ).filter(
        Q(current_period_start__isnull=True)
        | Q(current_period_end__isnull=True)
        | Q(current_period_end__lte=now)
        | Q(next_billing_at__lte=now)
    )

    result = {"checked": 0, "reconciled": 0, "failed": 0}
    processor = StripeWebhookProcessor()
    for subscription in subscriptions.iterator():
        result["checked"] += 1
        try:
            processor.reconcile_subscription(
                provider_subscription_id=subscription.provider_subscription_id
            )
            result["reconciled"] += 1
        except StripeServiceError:
            result["failed"] += 1
            logger.exception(
                "Daily Stripe reconciliation failed for subscription %s",
                subscription.id,
            )
    return result
