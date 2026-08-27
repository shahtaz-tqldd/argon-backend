import logging
from decimal import Decimal

import stripe
from django.conf import settings

from subscription.choices import BillingInterval


logger = logging.getLogger("app.subscription.stripe")


STRIPE_ZERO_DECIMAL_CURRENCIES = {
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


def stripe_minor_unit_amount(amount, currency):
    multiplier = (
        Decimal("1")
        if currency.strip().upper() in STRIPE_ZERO_DECIMAL_CURRENCIES
        else Decimal("100")
    )
    minor_amount = Decimal(amount) * multiplier
    if minor_amount != minor_amount.to_integral_value():
        raise StripeConfigurationError(
            "The local price has more precision than Stripe supports for its currency."
        )
    return int(minor_amount)


def stripe_recurring_interval(billing_interval):
    intervals = {
        BillingInterval.MONTHLY: "month",
        BillingInterval.ANNUAL: "year",
    }
    try:
        return intervals[billing_interval]
    except KeyError as exc:
        raise StripeConfigurationError(
            "Stripe checkout supports monthly or annual local prices only."
        ) from exc


class StripeServiceError(Exception):
    """A safe application-level error for Stripe API failures."""


class StripeConfigurationError(StripeServiceError):
    """Raised when required Stripe credentials are not configured."""


class StripeWebhookError(StripeServiceError):
    """Raised when a Stripe webhook cannot be authenticated or decoded."""


class StripePaymentRequiredError(StripeServiceError):
    """Raised when Stripe cannot charge an existing payment method."""


def stripe_object_to_dict(value):
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "to_dict_recursive"):
        return value.to_dict_recursive()
    raise TypeError("Stripe returned an unsupported response object.")


def stripe_expandable_id(value):
    if isinstance(value, dict):
        return value.get("id", "")
    return value or ""


class StripeBillingService:
    """Small boundary around Stripe Billing and Embedded Checkout."""

    def __init__(self, *, secret_key=None, webhook_secret=None):
        self.secret_key = secret_key or settings.STRIPE_SECRET_KEY
        self.webhook_secret = webhook_secret or settings.STRIPE_WEBHOOK_SECRET

    def _client(self):
        if not self.secret_key:
            raise StripeConfigurationError("Stripe is not configured.")
        return stripe.StripeClient(self.secret_key)

    def create_checkout_session(
        self,
        *,
        subscription,
        user,
        idempotency_key,
        customer_id="",
    ):
        plan = subscription.snapshot["plan"]
        pricing = subscription.snapshot["pricing"]

        metadata = {
            "argon_subscription_id": str(subscription.id),
            "argon_chatbot_id": str(subscription.chatbot_id),
            "argon_plan_price_id": str(subscription.plan_price_id),
            "argon_user_id": str(user.id),
        }
        line_items = [
            {
                "price_data": {
                    "currency": pricing["currency"].strip().lower(),
                    "product_data": {
                        "name": plan["name"],
                        "metadata": {
                            "argon_plan_id": plan["id"],
                            "argon_plan_price_id": pricing["plan_price_id"],
                        },
                    },
                    "recurring": {
                        "interval": stripe_recurring_interval(
                            pricing["billing_interval"]
                        ),
                    },
                    "unit_amount": stripe_minor_unit_amount(
                        pricing["amount"],
                        pricing["currency"],
                    ),
                },
                "quantity": 1,
            }
        ]

        params = {
            "mode": "subscription",
            "ui_mode": "embedded_page",
            "redirect_on_completion": "if_required",
            "return_url": settings.STRIPE_CHECKOUT_SUCCESS_URL,
            "client_reference_id": str(subscription.id),
            "line_items": line_items,
            "metadata": metadata,
            "subscription_data": {"metadata": metadata},
        }
        if customer_id:
            params["customer"] = customer_id
        else:
            params["customer_email"] = user.email

        try:
            session = self._client().v1.checkout.sessions.create(
                params,
                options={"idempotency_key": idempotency_key},
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe Checkout Session creation failed")
            raise StripeServiceError(
                "Stripe checkout is temporarily unavailable."
            ) from exc
        return stripe_object_to_dict(session)

    def retrieve_checkout_session(self, *, session_id):
        try:
            session = self._client().v1.checkout.sessions.retrieve(session_id)
        except stripe.StripeError as exc:
            logger.exception("Stripe Checkout Session retrieval failed")
            raise StripeServiceError(
                "Stripe checkout is temporarily unavailable."
            ) from exc
        return stripe_object_to_dict(session)

    def expire_checkout_session(self, *, session_id):
        try:
            session = self._client().v1.checkout.sessions.expire(session_id)
        except stripe.StripeError as exc:
            logger.exception("Stripe Checkout Session expiration failed")
            raise StripeServiceError(
                "Stripe could not replace the existing checkout."
            ) from exc
        return stripe_object_to_dict(session)

    def change_subscription_plan(
        self,
        *,
        provider_subscription_id,
        replacement_subscription,
        idempotency_key,
    ):
        """Change the one Stripe item using an inline, backend-owned price."""
        client = self._client()
        try:
            current = client.v1.subscriptions.retrieve(
                provider_subscription_id,
                {"expand": ["items.data.price.product"]},
            )
            current = stripe_object_to_dict(current)
            items = (current.get("items") or {}).get("data", [])
            if len(items) != 1:
                raise StripeConfigurationError(
                    "The Stripe subscription must contain exactly one plan item."
                )

            item = items[0]
            item_id = item.get("id", "")
            price = item.get("price") or {}
            product_id = stripe_expandable_id(
                price.get("product") if isinstance(price, dict) else ""
            )
            if not item_id or not product_id:
                raise StripeConfigurationError(
                    "Stripe did not return the subscription item and product."
                )

            pricing = replacement_subscription.snapshot["pricing"]
            metadata = {
                **(current.get("metadata") or {}),
                "argon_subscription_id": str(replacement_subscription.id),
                "argon_chatbot_id": str(replacement_subscription.chatbot_id),
                "argon_plan_price_id": str(
                    replacement_subscription.plan_price_id
                ),
            }
            updated = client.v1.subscriptions.update(
                provider_subscription_id,
                {
                    "items": [
                        {
                            "id": item_id,
                            "price_data": {
                                "currency": pricing["currency"].strip().lower(),
                                "product": product_id,
                                "recurring": {
                                    "interval": stripe_recurring_interval(
                                        pricing["billing_interval"]
                                    )
                                },
                                "unit_amount": stripe_minor_unit_amount(
                                    pricing["amount"],
                                    pricing["currency"],
                                ),
                            },
                            "quantity": 1,
                        }
                    ],
                    "cancel_at_period_end": False,
                    "metadata": metadata,
                    "payment_behavior": "error_if_incomplete",
                    "proration_behavior": "always_invoice",
                    "expand": ["items.data.price.product", "latest_invoice"],
                },
                options={"idempotency_key": idempotency_key},
            )
        except stripe.CardError as exc:
            logger.exception("Stripe Subscription plan change payment failed")
            raise StripePaymentRequiredError(
                "Stripe could not charge the saved payment method. Update the "
                "payment method in the billing portal and try again."
            ) from exc
        except stripe.StripeError as exc:
            logger.exception("Stripe Subscription plan change failed")
            raise StripeServiceError(
                "Stripe could not change the subscription plan."
            ) from exc
        return stripe_object_to_dict(updated)

    def cancel_subscription(self, *, subscription_id):
        try:
            subscription = self._client().v1.subscriptions.cancel(
                subscription_id,
                {"invoice_now": False, "prorate": False},
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe Subscription cancellation failed")
            raise StripeServiceError(
                "Stripe could not cancel the failed subscription."
            ) from exc
        return stripe_object_to_dict(subscription)

    def create_portal_session(self, *, customer_id):
        try:
            session = self._client().v1.billing_portal.sessions.create(
                {
                    "customer": customer_id,
                    "return_url": settings.STRIPE_BILLING_PORTAL_RETURN_URL,
                }
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe billing portal Session creation failed")
            raise StripeServiceError(
                "Stripe billing management is temporarily unavailable."
            ) from exc
        return stripe_object_to_dict(session)

    def set_cancel_at_period_end(self, *, subscription_id, cancel):
        try:
            subscription = self._client().v1.subscriptions.update(
                subscription_id,
                {"cancel_at_period_end": cancel},
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe Subscription cancellation update failed")
            raise StripeServiceError(
                "Stripe could not update the subscription cancellation."
            ) from exc
        return stripe_object_to_dict(subscription)

    def construct_webhook_event(self, *, payload, signature):
        if not self.webhook_secret:
            raise StripeConfigurationError("Stripe webhook signing is not configured.")
        if not self.webhook_secret.startswith("whsec_"):
            raise StripeConfigurationError(
                "STRIPE_WEBHOOK_SECRET must be a Stripe endpoint signing secret "
                "starting with 'whsec_'."
            )
        if not signature:
            raise StripeWebhookError("The Stripe-Signature header is required.")
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                self.webhook_secret,
                api_key=self.secret_key or None,
            )
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise StripeWebhookError("Invalid Stripe webhook signature.") from exc
        return stripe_object_to_dict(event)
