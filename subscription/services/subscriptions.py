from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone

from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription, Payment, PlanPrice
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


class DefaultFreePlanNotConfigured(LookupError):
    """Raised when the default free subscription price has not been provisioned."""


def get_default_free_plan_price():
    try:
        return PlanPrice.objects.select_related("plan").get(
            plan__slug="free",
            plan__is_free=True,
            plan__is_active=True,
            provider=PaymentProvider.MANUAL,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=0,
            is_active=True,
        )
    except PlanPrice.DoesNotExist as exc:
        raise DefaultFreePlanNotConfigured(
            "The active free/manual/monthly/USD subscription price is missing. "
            "Run create_subscription_plan before creating chatbots."
        ) from exc


@dataclass(frozen=True)
class StripeCheckoutResult:
    subscription: ChatbotSubscription
    client_secret: str | None
    reused: bool
    action: str


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


def _object_id(value):
    if isinstance(value, dict):
        return value.get("id", "")
    return value or ""


def _timestamp(value):
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


def _subscription_period(stripe_subscription):
    items = (stripe_subscription.get("items") or {}).get("data", [])
    item = items[0] if items else {}
    return (
        _timestamp(
            item.get(
                "current_period_start",
                stripe_subscription.get("current_period_start"),
            )
        ),
        _timestamp(
            item.get(
                "current_period_end",
                stripe_subscription.get("current_period_end"),
            )
        ),
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


def _create_incomplete_subscription(*, chatbot, plan_price, user):
    idempotency_key = f"checkout-{uuid4()}"
    try:
        return ChatbotSubscription.objects.create(
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


@transaction.atomic
def _replace_incomplete_subscription(*, subscription, plan_price, user):
    locked = ChatbotSubscription.objects.select_for_update().get(
        pk=subscription.pk
    )
    if locked.status not in {
        SubscriptionStatus.INCOMPLETE,
        SubscriptionStatus.CANCELED,
    }:
        raise SubscriptionConflictError(
            "The subscription changed while checkout was being replaced. Try again."
        )

    if locked.status == SubscriptionStatus.INCOMPLETE:
        now = timezone.now()
        locked.status = SubscriptionStatus.CANCELED
        locked.canceled_at = now
        locked.ended_at = now
        locked.provider_metadata = {
            **(locked.provider_metadata or {}),
            "checkout_status": "replaced",
        }
        locked.updated_by = user
        locked.save(
            update_fields=[
                "status",
                "canceled_at",
                "ended_at",
                "provider_metadata",
                "updated_by",
                "updated_at",
            ]
        )
    return _create_incomplete_subscription(
        chatbot=locked.chatbot,
        plan_price=plan_price,
        user=user,
    )


@transaction.atomic
def _activate_completed_checkout(*, subscription, checkout, user):
    locked = ChatbotSubscription.objects.select_for_update().get(
        pk=subscription.pk
    )
    if locked.status != SubscriptionStatus.INCOMPLETE:
        return locked

    provider_subscription_id = _object_id(checkout.get("subscription"))
    if not provider_subscription_id:
        raise SubscriptionConflictError(
            "Stripe has completed the checkout, but its subscription is not ready yet. "
            "Refresh the subscription status shortly."
        )

    now = timezone.now()
    locked.status = SubscriptionStatus.ACTIVE
    locked.started_at = locked.started_at or now
    locked.provider_customer_id = _object_id(checkout.get("customer"))
    locked.provider_subscription_id = provider_subscription_id
    locked.provider_metadata = {
        **(locked.provider_metadata or {}),
        "checkout_session_id": checkout.get("id", ""),
        "checkout_status": "complete",
        "checkout_payment_status": checkout.get("payment_status", ""),
        "checkout_async_payment_failed": False,
    }
    locked.updated_by = user
    locked.save(
        update_fields=[
            "status",
            "started_at",
            "provider_customer_id",
            "provider_subscription_id",
            "provider_metadata",
            "updated_by",
            "updated_at",
        ]
    )
    return locked


def _change_active_stripe_plan(
    *, subscription, plan_price, user, stripe_service
):
    if subscription.plan_price_id == plan_price.id:
        return StripeCheckoutResult(
            subscription=subscription,
            client_secret=None,
            reused=True,
            action="already_active",
        )
    if (
        subscription.status != SubscriptionStatus.ACTIVE
        or subscription.provider != PaymentProvider.STRIPE
        or not subscription.provider_subscription_id
    ):
        raise SubscriptionConflictError(
            "This subscription cannot change plans until its payment issue is "
            "resolved in the billing portal."
        )

    target_plan_price_id = str(plan_price.id)
    with transaction.atomic():
        locked = ChatbotSubscription.objects.select_for_update().get(
            pk=subscription.pk
        )
        if locked.status != SubscriptionStatus.ACTIVE:
            existing_replacement = (
                ChatbotSubscription.objects.filter(
                    chatbot=locked.chatbot,
                    plan_price=plan_price,
                    provider_subscription_id=subscription.provider_subscription_id,
                    status=SubscriptionStatus.ACTIVE,
                )
                .order_by("-created_at")
                .first()
            )
            if existing_replacement is not None:
                return StripeCheckoutResult(
                    subscription=existing_replacement,
                    client_secret=None,
                    reused=True,
                    action="already_active",
                )
            raise SubscriptionConflictError(
                "The subscription changed while the plan change was starting. "
                "Refresh the subscription status."
            )
        plan_change_metadata = locked.provider_metadata or {}
        plan_change_status = plan_change_metadata.get("plan_change_status")
        processing_target = plan_change_metadata.get("plan_change_target_price_id")
        if (
            plan_change_status == "processing"
            and processing_target
            and processing_target != target_plan_price_id
        ):
            raise SubscriptionConflictError(
                "Another subscription plan change is already processing. Refresh "
                "the subscription status before trying again."
            )
        if plan_change_status == "processing" and (
            processing_target == target_plan_price_id
        ):
            try:
                replacement_id = UUID(
                    plan_change_metadata.get("plan_change_subscription_id", "")
                )
            except (TypeError, ValueError, AttributeError):
                replacement_id = uuid4()
        else:
            replacement_id = uuid4()
        locked.provider_metadata = {
            **plan_change_metadata,
            "plan_change_status": "processing",
            "plan_change_target_price_id": target_plan_price_id,
            "plan_change_subscription_id": str(replacement_id),
            "plan_change_error": "",
        }
        locked.updated_by = user
        locked.save(
            update_fields=["provider_metadata", "updated_by", "updated_at"]
        )
        subscription = locked

    replacement = ChatbotSubscription(
        id=replacement_id,
        chatbot=subscription.chatbot,
        plan_price=plan_price,
        selected_by=user,
        provider=PaymentProvider.STRIPE,
        renewal_mode=RenewalMode.PROVIDER_MANAGED,
        status=SubscriptionStatus.ACTIVE,
    )
    replacement.capture_plan_snapshot()
    idempotency_key = f"plan-change-{replacement_id}"
    try:
        stripe_subscription = stripe_service.change_subscription_plan(
            provider_subscription_id=subscription.provider_subscription_id,
            replacement_subscription=replacement,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        _merge_provider_metadata(
            subscription,
            {
                "plan_change_status": "failed",
                "plan_change_error": str(exc),
            },
            updated_by=user,
        )
        raise
    if stripe_subscription.get("status") not in {"active", "trialing"}:
        _merge_provider_metadata(
            subscription,
            {
                "plan_change_status": "failed",
                "plan_change_error": (
                    "Stripe returned subscription status "
                    f"{stripe_subscription.get('status', 'unknown')}."
                ),
            },
            updated_by=user,
        )
        raise SubscriptionConflictError(
            "Stripe did not confirm the plan change. Refresh the subscription "
            "status before trying again."
        )

    with transaction.atomic():
        current = ChatbotSubscription.objects.select_for_update().get(
            pk=subscription.pk
        )
        if current.status != SubscriptionStatus.ACTIVE:
            existing_replacement = (
                ChatbotSubscription.objects.filter(
                    chatbot=current.chatbot,
                    plan_price=plan_price,
                    provider_subscription_id=subscription.provider_subscription_id,
                    status=SubscriptionStatus.ACTIVE,
                )
                .order_by("-created_at")
                .first()
            )
            if existing_replacement is not None:
                return StripeCheckoutResult(
                    subscription=existing_replacement,
                    client_secret=None,
                    reused=True,
                    action="already_active",
                )
            raise SubscriptionConflictError(
                "The subscription changed while Stripe was updating it. Refresh "
                "the subscription status."
            )

        now = timezone.now()
        current.status = SubscriptionStatus.CANCELED
        current.canceled_at = now
        current.ended_at = now
        current.cancel_at_period_end = False
        current.provider_metadata = {
            **(current.provider_metadata or {}),
            "plan_change_status": "complete",
            "plan_changed_to_subscription_id": str(replacement_id),
        }
        current.updated_by = user
        current.save(
            update_fields=[
                "status",
                "canceled_at",
                "ended_at",
                "cancel_at_period_end",
                "provider_metadata",
                "updated_by",
                "updated_at",
            ]
        )

        period_start, period_end = _subscription_period(stripe_subscription)
        replacement.provider_customer_id = (
            _object_id(stripe_subscription.get("customer"))
            or current.provider_customer_id
        )
        replacement.provider_subscription_id = current.provider_subscription_id
        replacement.started_at = now
        replacement.current_period_start = (
            period_start or current.current_period_start
        )
        replacement.current_period_end = period_end or current.current_period_end
        replacement.cancel_at_period_end = bool(
            stripe_subscription.get("cancel_at_period_end", False)
        )
        replacement.provider_metadata = {
            "plan_change_from_subscription_id": str(current.id),
            "plan_change_idempotency_key": idempotency_key,
            "stripe_status": stripe_subscription.get("status", ""),
            "latest_invoice_id": _object_id(
                stripe_subscription.get("latest_invoice")
            ),
        }
        replacement.created_by = user
        replacement.updated_by = user
        replacement.save(force_insert=True)
        latest_invoice_id = _object_id(
            stripe_subscription.get("latest_invoice")
        )
        if latest_invoice_id:
            Payment.objects.filter(
                provider=PaymentProvider.STRIPE,
                provider_reference=latest_invoice_id,
            ).update(
                subscription=replacement,
                plan_price=plan_price,
                updated_at=now,
            )

    return StripeCheckoutResult(
        subscription=replacement,
        client_secret=None,
        reused=False,
        action="plan_changed",
    )


def start_stripe_checkout(*, chatbot, plan_price, user, stripe_service=None):
    stripe_service = stripe_service or StripeBillingService()

    with transaction.atomic():
        subscription = None
        active_plan_change = None
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
            if (
                existing.status == SubscriptionStatus.INCOMPLETE
                and existing.provider == PaymentProvider.STRIPE
            ):
                subscription = existing
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
            elif (
                existing.status == SubscriptionStatus.ACTIVE
                and existing.provider == PaymentProvider.STRIPE
            ):
                active_plan_change = existing
            else:
                raise SubscriptionConflictError(
                    "This subscription has a payment issue. Resolve it in the "
                    "billing portal before starting another checkout."
                )
        if subscription is None and active_plan_change is None:
            subscription = _create_incomplete_subscription(
                chatbot=chatbot,
                plan_price=plan_price,
                user=user,
            )

    if active_plan_change is not None:
        return _change_active_stripe_plan(
            subscription=active_plan_change,
            plan_price=plan_price,
            user=user,
            stripe_service=stripe_service,
        )

    metadata = subscription.provider_metadata or {}
    checkout_client_secret = metadata.get("checkout_client_secret")
    checkout_session_id = metadata.get("checkout_session_id")
    same_plan = subscription.plan_price_id == plan_price.id

    if checkout_session_id:
        existing_checkout = stripe_service.retrieve_checkout_session(
            session_id=checkout_session_id,
        )
        checkout_status = existing_checkout.get("status")

        if checkout_status == "open" and same_plan and checkout_client_secret:
            return StripeCheckoutResult(
                subscription=subscription,
                client_secret=checkout_client_secret,
                reused=True,
                action="checkout",
            )

        if checkout_status == "open":
            stripe_service.expire_checkout_session(
                session_id=checkout_session_id
            )
            if not same_plan:
                subscription = _replace_incomplete_subscription(
                    subscription=subscription,
                    plan_price=plan_price,
                    user=user,
                )
                metadata = subscription.provider_metadata or {}
                checkout_session_id = ""
                checkout_client_secret = ""
            else:
                existing_checkout = {**existing_checkout, "status": "expired"}
                checkout_status = "expired"

        if checkout_status == "complete":
            payment_status = existing_checkout.get("payment_status")
            _merge_provider_metadata(
                subscription,
                {
                    "checkout_status": "complete",
                    "checkout_payment_status": payment_status or "",
                },
                updated_by=user,
            )
            if payment_status in {"paid", "no_payment_required"}:
                subscription = _activate_completed_checkout(
                    subscription=subscription,
                    checkout=existing_checkout,
                    user=user,
                )
                if same_plan:
                    return StripeCheckoutResult(
                        subscription=subscription,
                        client_secret=None,
                        reused=True,
                        action="subscription_activated",
                    )
                return _change_active_stripe_plan(
                    subscription=subscription,
                    plan_price=plan_price,
                    user=user,
                    stripe_service=stripe_service,
                )

            if metadata.get("checkout_async_payment_failed"):
                provider_subscription_id = (
                    _object_id(existing_checkout.get("subscription"))
                    or subscription.provider_subscription_id
                )
                if provider_subscription_id:
                    stripe_service.cancel_subscription(
                        subscription_id=provider_subscription_id
                    )
                subscription = _replace_incomplete_subscription(
                    subscription=subscription,
                    plan_price=plan_price,
                    user=user,
                )
                metadata = subscription.provider_metadata or {}
                checkout_session_id = ""
                checkout_client_secret = ""
            else:
                raise SubscriptionConflictError(
                    "Stripe is still processing this checkout payment. Refresh the "
                    "subscription status shortly."
                )

        elif checkout_status not in {"expired", "open"}:
            raise SubscriptionConflictError(
                "This checkout cannot be restarted in its current state."
            )

        if checkout_status == "expired":
            if not same_plan:
                subscription = _replace_incomplete_subscription(
                    subscription=subscription,
                    plan_price=plan_price,
                    user=user,
                )
            else:
                replacement_key = f"checkout-{uuid4()}"
                with transaction.atomic():
                    locked = ChatbotSubscription.objects.select_for_update().get(
                        pk=subscription.pk
                    )
                    latest_metadata = locked.provider_metadata or {}
                    if latest_metadata.get("checkout_session_id") != checkout_session_id:
                        latest_client_secret = latest_metadata.get(
                            "checkout_client_secret"
                        )
                        if latest_client_secret:
                            return StripeCheckoutResult(
                                subscription=locked,
                                client_secret=latest_client_secret,
                                reused=True,
                                action="checkout",
                            )
                        raise SubscriptionConflictError(
                            "A new checkout is already being prepared. Please try again."
                        )
                    locked.provider_metadata = {
                        **latest_metadata,
                        "checkout_idempotency_key": replacement_key,
                        "checkout_session_id": "",
                        "checkout_client_secret": "",
                        "checkout_status": "replacing",
                        "checkout_error": "",
                    }
                    locked.updated_by = user
                    locked.save(
                        update_fields=["provider_metadata", "updated_by", "updated_at"]
                    )
                    subscription = locked
            metadata = subscription.provider_metadata or {}

    elif not same_plan:
        if subscription.provider_subscription_id:
            raise SubscriptionConflictError(
                "Stripe is still preparing the existing subscription. Refresh the "
                "subscription status before changing plans."
            )
        subscription = _replace_incomplete_subscription(
            subscription=subscription,
            plan_price=plan_price,
            user=user,
        )
        metadata = subscription.provider_metadata or {}

    idempotency_key = metadata.get("checkout_idempotency_key")
    if metadata.get("checkout_url") and not metadata.get(
        "checkout_client_secret"
    ):
        # A pre-embedded incomplete checkout cannot be reused because Stripe
        # idempotency keys bind to the original hosted Session parameters.
        idempotency_key = f"checkout-{uuid4()}"
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

    checkout_client_secret = checkout.get("client_secret")
    if not checkout_client_secret:
        raise SubscriptionConflictError(
            "Stripe did not return an embedded Checkout client secret."
        )

    subscription = _merge_provider_metadata(
        subscription,
        {
            "checkout_idempotency_key": idempotency_key,
            "checkout_session_id": checkout.get("id", ""),
            "checkout_client_secret": checkout_client_secret,
            "checkout_url": "",
            "checkout_status": checkout.get("status", "open"),
            "checkout_error": "",
        },
        updated_by=user,
    )
    return StripeCheckoutResult(
        subscription=subscription,
        client_secret=checkout_client_secret,
        reused=False,
        action="checkout",
    )


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
