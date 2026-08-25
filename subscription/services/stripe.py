import logging

import stripe
from django.conf import settings


logger = logging.getLogger("app.subscription.stripe")


class StripeServiceError(Exception):
    """A safe application-level error for Stripe API failures."""


class StripeConfigurationError(StripeServiceError):
    """Raised when required Stripe credentials are not configured."""


class StripeWebhookError(StripeServiceError):
    """Raised when a Stripe webhook cannot be authenticated or decoded."""


def stripe_object_to_dict(value):
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "to_dict_recursive"):
        return value.to_dict_recursive()
    raise TypeError("Stripe returned an unsupported response object.")


class StripeBillingService:
    """Small boundary around Stripe Billing and hosted Checkout."""

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
        plan_price = subscription.plan_price
        if not plan_price.provider_price_id:
            raise StripeConfigurationError(
                "This subscription price is not configured in Stripe."
            )

        metadata = {
            "argon_subscription_id": str(subscription.id),
            "argon_chatbot_id": str(subscription.chatbot_id),
            "argon_plan_price_id": str(subscription.plan_price_id),
            "argon_user_id": str(user.id),
        }
        line_items = [{"price": plan_price.provider_price_id, "quantity": 1}]
        if plan_price.provider_overage_price_id:
            line_items.append({"price": plan_price.provider_overage_price_id})

        params = {
            "mode": "subscription",
            "success_url": settings.STRIPE_CHECKOUT_SUCCESS_URL,
            "cancel_url": settings.STRIPE_CHECKOUT_CANCEL_URL,
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
