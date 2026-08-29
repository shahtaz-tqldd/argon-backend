import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from subscription.choices import (
    PaymentProvider,
    PaymentStatus,
    PaymentType,
    SubscriptionStatus,
    WebhookProcessingStatus,
)
from subscription.models import (
    ChatbotSubscription,
    Payment,
    PaymentWebhookEvent,
)
from subscription.services.stripe import StripeBillingService


logger = logging.getLogger("app.subscription.webhooks")


def _apply_subscription_capacity(subscription):
    # Imported lazily to avoid the chatbot/subscription service import cycle.
    from chatbot.services.capacity import (
        apply_active_subscription_to_chatbot_capacity,
    )

    return apply_active_subscription_to_chatbot_capacity(subscription)


HANDLED_EVENT_TYPES = {
    "checkout.session.completed",
    "checkout.session.async_payment_failed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.expired",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
    "invoice.payment_action_required",
    "charge.refunded",
}


STRIPE_SUBSCRIPTION_STATUS_MAP = {
    "incomplete": SubscriptionStatus.INCOMPLETE,
    "incomplete_expired": SubscriptionStatus.CANCELED,
    "trialing": SubscriptionStatus.ACTIVE,
    "active": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE,
    "paused": SubscriptionStatus.PAUSED,
    "canceled": SubscriptionStatus.CANCELED,
    "unpaid": SubscriptionStatus.UNPAID,
}


STRIPE_ZERO_DECIMAL_CHARGE_CURRENCIES = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "JPY",
    "KMF",
    "KRW",
    "MGA",
    "PYG",
    "RWF",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}


def _object_id(value):
    if isinstance(value, dict):
        return value.get("id", "")
    return value or ""


def _timestamp(value):
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


def _valid_uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _invoice_subscription_details(invoice):
    parent = invoice.get("parent") or {}
    details = parent.get("subscription_details") or {}
    if details:
        return details
    return {
        "subscription": invoice.get("subscription"),
        "metadata": (invoice.get("subscription_details") or {}).get(
            "metadata", {}
        ),
    }


def _payment_intent_id(invoice):
    legacy_payment_intent = _object_id(invoice.get("payment_intent"))
    if legacy_payment_intent:
        return legacy_payment_intent

    for invoice_payment in (invoice.get("payments") or {}).get("data", []):
        payment = invoice_payment.get("payment") or {}
        payment_intent = _object_id(payment.get("payment_intent"))
        if payment_intent:
            return payment_intent
    return ""


def _minor_units_to_decimal(amount, currency):
    divisor = (
        Decimal("1")
        if (currency or "").upper() in STRIPE_ZERO_DECIMAL_CHARGE_CURRENCIES
        else Decimal("100")
    )
    return (Decimal(int(amount or 0)) / divisor).quantize(Decimal("0.01"))


def _subscription_from_metadata(metadata, *, provider_subscription_id=""):
    internal_id = _valid_uuid((metadata or {}).get("argon_subscription_id"))
    queryset = ChatbotSubscription.objects.select_related(
        "plan_price",
        "selected_by",
    ).select_for_update(
        of=("self",),
    )
    if provider_subscription_id:
        subscription = queryset.filter(
            provider=PaymentProvider.STRIPE,
            provider_subscription_id=provider_subscription_id,
        ).first()
        if subscription is not None:
            return subscription
    if internal_id is not None:
        return queryset.filter(pk=internal_id).first()
    return None


def _subscription_period(stripe_subscription):
    items = (stripe_subscription.get("items") or {}).get("data", [])
    selected_item = items[0] if items else {}

    period_start = selected_item.get(
        "current_period_start",
        stripe_subscription.get("current_period_start"),
    )
    period_end = selected_item.get(
        "current_period_end",
        stripe_subscription.get("current_period_end"),
    )
    return _timestamp(period_start), _timestamp(period_end)


class StripeWebhookProcessor:
    """Authenticate and idempotently apply Stripe snapshot events."""

    def __init__(self, *, stripe_service=None):
        self.stripe_service = stripe_service or StripeBillingService()

    def process(self, *, payload, signature):
        event = self.stripe_service.construct_webhook_event(
            payload=payload,
            signature=signature,
        )
        event_id = event["id"]
        event_type = event["type"]

        with transaction.atomic():
            webhook_event, _ = PaymentWebhookEvent.objects.get_or_create(
                provider=PaymentProvider.STRIPE,
                provider_event_id=event_id,
                defaults={
                    "event_type": event_type,
                    "api_version": event.get("api_version") or "",
                    "livemode": bool(event.get("livemode", False)),
                    "payload": event,
                },
            )
            webhook_event = PaymentWebhookEvent.objects.select_for_update().get(
                pk=webhook_event.pk
            )
            if webhook_event.processing_status in {
                WebhookProcessingStatus.PROCESSED,
                WebhookProcessingStatus.IGNORED,
                WebhookProcessingStatus.PROCESSING,
            }:
                return webhook_event, True

            webhook_event.processing_status = WebhookProcessingStatus.PROCESSING
            webhook_event.attempts += 1
            webhook_event.last_error = ""
            webhook_event.save(
                update_fields=[
                    "processing_status",
                    "attempts",
                    "last_error",
                    "updated_at",
                ]
            )

        try:
            with transaction.atomic():
                payment = self._dispatch(
                    event_type,
                    event["data"]["object"],
                    event_created=event.get("created"),
                )
        except Exception as exc:
            logger.exception("Stripe webhook processing failed for %s", event_id)
            PaymentWebhookEvent.objects.filter(pk=webhook_event.pk).update(
                processing_status=WebhookProcessingStatus.FAILED,
                last_error=str(exc),
                updated_at=timezone.now(),
            )
            raise

        final_status = (
            WebhookProcessingStatus.PROCESSED
            if event_type in HANDLED_EVENT_TYPES
            else WebhookProcessingStatus.IGNORED
        )
        PaymentWebhookEvent.objects.filter(pk=webhook_event.pk).update(
            processing_status=final_status,
            payment=payment,
            processed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        webhook_event.refresh_from_db()
        return webhook_event, False

    def _dispatch(self, event_type, data, *, event_created=None):
        if event_type in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        }:
            return self._checkout_completed(data)
        if event_type == "checkout.session.async_payment_failed":
            return self._checkout_failed(data)
        if event_type == "checkout.session.expired":
            return self._checkout_expired(data)
        if event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            return self._sync_subscription(data, event_created=event_created)
        if event_type in {
            "invoice.paid",
            "invoice.payment_succeeded",
            "invoice.payment_failed",
            "invoice.payment_action_required",
        }:
            return self._sync_invoice(data, event_type)
        if event_type == "charge.refunded":
            return self._sync_refund(data)
        return None

    def _checkout_completed(self, checkout):
        metadata = checkout.get("metadata") or {}
        provider_subscription_id = _object_id(checkout.get("subscription"))
        subscription = _subscription_from_metadata(
            metadata,
            provider_subscription_id=provider_subscription_id,
        )
        if subscription is None:
            return None

        payment_status = checkout.get("payment_status")
        was_active = subscription.status == SubscriptionStatus.ACTIVE
        if (
            payment_status in {"paid", "no_payment_required"}
            and subscription.status == SubscriptionStatus.INCOMPLETE
        ):
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.started_at = subscription.started_at or timezone.now()
        subscription.provider_customer_id = _object_id(checkout.get("customer"))
        subscription.provider_subscription_id = provider_subscription_id or None
        subscription.provider_metadata = {
            **(subscription.provider_metadata or {}),
            "checkout_session_id": checkout.get("id", ""),
            "checkout_status": checkout.get("status", "complete"),
            "checkout_payment_status": payment_status or "",
            "checkout_async_payment_failed": False,
        }
        subscription.save(
            update_fields=[
                "status",
                "started_at",
                "provider_customer_id",
                "provider_subscription_id",
                "provider_metadata",
                "updated_at",
            ]
        )
        if (
            not was_active
            and subscription.status == SubscriptionStatus.ACTIVE
        ):
            _apply_subscription_capacity(subscription)
        return None

    def _checkout_failed(self, checkout):
        metadata = checkout.get("metadata") or {}
        provider_subscription_id = _object_id(checkout.get("subscription"))
        subscription = _subscription_from_metadata(
            metadata,
            provider_subscription_id=provider_subscription_id,
        )
        if subscription is None or subscription.status != SubscriptionStatus.INCOMPLETE:
            return None

        subscription.provider_customer_id = _object_id(checkout.get("customer"))
        subscription.provider_subscription_id = provider_subscription_id or None
        subscription.provider_metadata = {
            **(subscription.provider_metadata or {}),
            "checkout_session_id": checkout.get("id", ""),
            "checkout_status": checkout.get("status", "complete"),
            "checkout_payment_status": "failed",
            "checkout_async_payment_failed": True,
        }
        subscription.save(
            update_fields=[
                "provider_customer_id",
                "provider_subscription_id",
                "provider_metadata",
                "updated_at",
            ]
        )
        return None

    def _checkout_expired(self, checkout):
        subscription = _subscription_from_metadata(checkout.get("metadata") or {})
        if subscription is None or subscription.status != SubscriptionStatus.INCOMPLETE:
            return None
        current_checkout_id = (subscription.provider_metadata or {}).get(
            "checkout_session_id"
        )
        if current_checkout_id != checkout.get("id"):
            # This subscription has already rotated to a replacement Session.
            # A delayed expiration event for the old Session must not cancel it.
            return None
        now = timezone.now()
        subscription.status = SubscriptionStatus.CANCELED
        subscription.ended_at = now
        subscription.provider_metadata = {
            **(subscription.provider_metadata or {}),
            "checkout_session_id": checkout.get("id", ""),
            "checkout_status": "expired",
        }
        subscription.save(
            update_fields=[
                "status",
                "ended_at",
                "provider_metadata",
                "updated_at",
            ]
        )
        return None

    def _sync_subscription(self, stripe_subscription, *, event_created=None):
        provider_subscription_id = stripe_subscription.get("id", "")
        subscription = _subscription_from_metadata(
            stripe_subscription.get("metadata") or {},
            provider_subscription_id=provider_subscription_id,
        )
        if subscription is None:
            return None

        previous_status = subscription.status
        previous_event_created = (subscription.provider_metadata or {}).get(
            "stripe_subscription_event_created"
        )
        if (
            event_created is not None
            and previous_event_created is not None
            and int(event_created) < int(previous_event_created)
        ):
            return None

        current_period_start, current_period_end = _subscription_period(
            stripe_subscription
        )
        status = STRIPE_SUBSCRIPTION_STATUS_MAP.get(
            stripe_subscription.get("status"),
            SubscriptionStatus.INCOMPLETE,
        )
        started_at = _timestamp(stripe_subscription.get("start_date"))
        canceled_at = _timestamp(stripe_subscription.get("canceled_at"))
        ended_at = _timestamp(stripe_subscription.get("ended_at"))

        subscription.status = status
        subscription.provider_customer_id = _object_id(
            stripe_subscription.get("customer")
        )
        subscription.provider_subscription_id = provider_subscription_id or None
        subscription.started_at = started_at or subscription.started_at
        subscription.current_period_start = current_period_start
        subscription.current_period_end = current_period_end
        subscription.cancel_at_period_end = bool(
            stripe_subscription.get("cancel_at_period_end", False)
        )
        subscription.canceled_at = canceled_at
        subscription.ended_at = ended_at
        if status == SubscriptionStatus.CANCELED and subscription.ended_at is None:
            subscription.ended_at = timezone.now()
        subscription.provider_metadata = {
            **(subscription.provider_metadata or {}),
            "stripe_status": stripe_subscription.get("status", ""),
            "stripe_subscription_event_created": event_created,
            "billing_sync_error": "",
            "latest_invoice_id": _object_id(
                stripe_subscription.get("latest_invoice")
            ),
        }
        subscription.save(
            update_fields=[
                "status",
                "provider_customer_id",
                "provider_subscription_id",
                "started_at",
                "current_period_start",
                "current_period_end",
                "cancel_at_period_end",
                "canceled_at",
                "ended_at",
                "provider_metadata",
                "updated_at",
            ]
        )
        if (
            previous_status != SubscriptionStatus.ACTIVE
            and subscription.status == SubscriptionStatus.ACTIVE
        ):
            _apply_subscription_capacity(subscription)
        return None

    def _sync_invoice(self, invoice, event_type):
        subscription_details = _invoice_subscription_details(invoice)
        provider_subscription_id = _object_id(
            subscription_details.get("subscription")
        )
        subscription = _subscription_from_metadata(
            subscription_details.get("metadata") or {},
            provider_subscription_id=provider_subscription_id,
        )
        if subscription is None:
            return None

        previous_status = subscription.status
        if provider_subscription_id and not subscription.provider_subscription_id:
            subscription.provider_subscription_id = provider_subscription_id
        customer_id = _object_id(invoice.get("customer"))
        if customer_id:
            subscription.provider_customer_id = customer_id

        if event_type in {"invoice.paid", "invoice.payment_succeeded"}:
            payment_status = PaymentStatus.SUCCEEDED
            if subscription.status != SubscriptionStatus.CANCELED:
                subscription.status = SubscriptionStatus.ACTIVE
                subscription.started_at = subscription.started_at or timezone.now()
            amount_minor = invoice.get("amount_paid", invoice.get("total", 0))
        elif event_type == "invoice.payment_action_required":
            payment_status = PaymentStatus.REQUIRES_ACTION
            if subscription.status != SubscriptionStatus.CANCELED:
                subscription.status = SubscriptionStatus.PAST_DUE
            amount_minor = invoice.get("amount_due", invoice.get("total", 0))
        else:
            payment_status = PaymentStatus.FAILED
            if subscription.status != SubscriptionStatus.CANCELED:
                subscription.status = SubscriptionStatus.PAST_DUE
            amount_minor = invoice.get("amount_due", invoice.get("total", 0))

        subscription.save(
            update_fields=[
                "provider_subscription_id",
                "provider_customer_id",
                "status",
                "started_at",
                "updated_at",
            ]
        )
        if (
            previous_status != SubscriptionStatus.ACTIVE
            and subscription.status == SubscriptionStatus.ACTIVE
        ):
            _apply_subscription_capacity(subscription)

        paid_at = _timestamp(
            (invoice.get("status_transitions") or {}).get("paid_at")
        )
        failure = invoice.get("last_payment_error") or {}
        currency = (invoice.get("currency") or "USD").upper()
        payment, _ = Payment.objects.update_or_create(
            provider=PaymentProvider.STRIPE,
            provider_reference=invoice["id"],
            defaults={
                "subscription": subscription,
                "plan_price": subscription.plan_price,
                "user": subscription.selected_by,
                "payment_type": PaymentType.SUBSCRIPTION,
                "status": payment_status,
                "billing_interval": subscription.get_billing_interval(),
                "amount": _minor_units_to_decimal(amount_minor, currency),
                "currency": currency,
                "description": invoice.get("description") or "Subscription invoice",
                "provider_customer_id": customer_id,
                "provider_metadata": {
                    "invoice_number": invoice.get("number") or "",
                    "hosted_invoice_url": invoice.get("hosted_invoice_url") or "",
                    "invoice_pdf": invoice.get("invoice_pdf") or "",
                    "billing_reason": invoice.get("billing_reason") or "",
                    "payment_intent": _payment_intent_id(invoice),
                },
                "failure_code": failure.get("code") or "",
                "failure_message": failure.get("message") or "",
                "paid_at": paid_at,
            },
        )
        return payment

    def _sync_refund(self, charge):
        payment_intent_id = _object_id(charge.get("payment_intent"))
        if not payment_intent_id:
            return None
        payment = Payment.objects.filter(
            provider=PaymentProvider.STRIPE,
            provider_metadata__payment_intent=payment_intent_id,
        ).first()
        if payment is None:
            return None

        amount_refunded = _minor_units_to_decimal(
            charge.get("amount_refunded", 0),
            charge.get("currency") or payment.currency,
        )
        payment.amount_refunded = min(amount_refunded, payment.amount)
        payment.status = (
            PaymentStatus.REFUNDED
            if bool(charge.get("refunded"))
            else PaymentStatus.PARTIALLY_REFUNDED
        )
        payment.refunded_at = timezone.now()
        payment.save(
            update_fields=[
                "amount_refunded",
                "status",
                "refunded_at",
                "updated_at",
            ]
        )
        return payment
