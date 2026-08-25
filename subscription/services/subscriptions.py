from uuid import uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone

from subscription.choices import (
    PaymentProvider,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription
from subscription.services.stripe import StripeBillingService


OPEN_SUBSCRIPTION_STATUSES = (
    SubscriptionStatus.INCOMPLETE,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
    SubscriptionStatus.PAUSED,
    SubscriptionStatus.UNPAID,
)


class SubscriptionConflictError(Exception):
    """Raised when a chatbot already has an incompatible open subscription."""


def get_open_subscription(chatbot):
    return (
        ChatbotSubscription.objects.filter(
            chatbot=chatbot,
            status__in=OPEN_SUBSCRIPTION_STATUSES,
        )
        .select_related("plan_price", "selected_by")
        .first()
    )


def _customer_id_for_chatbot(chatbot):
    return (
        ChatbotSubscription.objects.filter(
            chatbot=chatbot,
            provider=PaymentProvider.STRIPE,
        )
        .exclude(provider_customer_id="")
        .values_list("provider_customer_id", flat=True)
        .first()
        or ""
    )


@transaction.atomic
def _merge_provider_metadata(subscription, values, *, updated_by=None):
    locked = ChatbotSubscription.objects.select_for_update().get(
        pk=subscription.pk
    )
    locked.provider_metadata = {
        **(locked.provider_metadata or {}),
        **values,
    }
    update_fields = ["provider_metadata", "updated_at"]
    if updated_by is not None:
        locked.updated_by = updated_by
        update_fields.append("updated_by")
    locked.save(update_fields=update_fields)
    return locked


def start_stripe_checkout(*, chatbot, plan_price, user, stripe_service=None):
    stripe_service = stripe_service or StripeBillingService()

    with transaction.atomic():
        subscription = None
        existing = (
            ChatbotSubscription.objects.select_for_update()
            .filter(
                chatbot=chatbot,
                status__in=OPEN_SUBSCRIPTION_STATUSES,
            )
            .select_related("plan_price")
            .first()
        )
        if existing is not None:
            checkout_url = (existing.provider_metadata or {}).get("checkout_url")
            if (
                existing.status == SubscriptionStatus.INCOMPLETE
                and existing.plan_price_id == plan_price.id
                and existing.provider == PaymentProvider.STRIPE
            ):
                subscription = existing
                if checkout_url:
                    return subscription, checkout_url, True
            elif existing.status == SubscriptionStatus.ACTIVE and existing.is_free_plan():
                now = timezone.now()
                existing.status = SubscriptionStatus.CANCELED
                existing.canceled_at = now
                existing.ended_at = now
                existing.updated_by = user
                existing.save(
                    update_fields=[
                        "status",
                        "canceled_at",
                        "ended_at",
                        "updated_by",
                        "updated_at",
                    ]
                )
            else:
                raise SubscriptionConflictError(
                    "This chatbot already has an open subscription."
                )
        if subscription is None:
            idempotency_key = f"checkout-{uuid4()}"
            try:
                subscription = ChatbotSubscription.objects.create(
                    chatbot=chatbot,
                    plan_price=plan_price,
                    selected_by=user,
                    provider=PaymentProvider.STRIPE,
                    renewal_mode=RenewalMode.PROVIDER_MANAGED,
                    status=SubscriptionStatus.INCOMPLETE,
                    provider_metadata={"checkout_idempotency_key": idempotency_key},
                    created_by=user,
                    updated_by=user,
                )
            except IntegrityError as exc:
                raise SubscriptionConflictError(
                    "This chatbot already has an open subscription."
                ) from exc

    metadata = subscription.provider_metadata or {}
    idempotency_key = metadata.get("checkout_idempotency_key")
    if not idempotency_key:
        idempotency_key = f"checkout-{uuid4()}"

    try:
        checkout = stripe_service.create_checkout_session(
            subscription=subscription,
            user=user,
            idempotency_key=idempotency_key,
            customer_id=_customer_id_for_chatbot(chatbot),
        )
    except Exception as exc:
        _merge_provider_metadata(
            subscription,
            {
                "checkout_idempotency_key": idempotency_key,
                "checkout_error": str(exc),
            },
        )
        raise

    checkout_url = checkout.get("url")
    if not checkout_url:
        raise SubscriptionConflictError("Stripe did not return a checkout URL.")

    subscription = _merge_provider_metadata(
        subscription,
        {
            "checkout_idempotency_key": idempotency_key,
            "checkout_session_id": checkout.get("id", ""),
            "checkout_url": checkout_url,
            "checkout_status": checkout.get("status", "open"),
            "checkout_error": "",
        },
        updated_by=user,
    )
    return subscription, checkout_url, False


@transaction.atomic
def activate_free_subscription(*, chatbot, plan_price, user):
    existing = (
        ChatbotSubscription.objects.select_for_update()
        .filter(
            chatbot=chatbot,
            status__in=OPEN_SUBSCRIPTION_STATUSES,
        )
        .first()
    )
    if existing is not None:
        if (
            existing.status == SubscriptionStatus.ACTIVE
            and existing.plan_price_id == plan_price.id
            and existing.is_free_plan()
        ):
            return existing, False
        raise SubscriptionConflictError(
            "This chatbot already has an open subscription."
        )

    now = timezone.now()
    subscription = ChatbotSubscription.objects.create(
        chatbot=chatbot,
        plan_price=plan_price,
        selected_by=user,
        provider=plan_price.provider,
        renewal_mode=RenewalMode.MANUAL,
        status=SubscriptionStatus.ACTIVE,
        started_at=now,
        created_by=user,
        updated_by=user,
    )
    return subscription, True
