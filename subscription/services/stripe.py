from app.utils.logger import logger
from decimal import Decimal
from hashlib import sha256
from time import time

import stripe
from django.conf import settings

from coupon.choices import DiscountDuration, DiscountType
from subscription.choices import BillingInterval


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
        promotion_code_id="",
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
        if promotion_code_id:
            params["discounts"] = [{"promotion_code": promotion_code_id}]
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

    def _promotion_fingerprint(self, coupon, billing_interval):
        discount = coupon.discount
        material = "|".join(
            str(value)
            for value in (
                discount.discount_type,
                discount.value,
                (discount.currency or "").upper(),
                discount.duration,
                discount.duration_in_billing_cycles or 0,
                billing_interval,
            )
        )
        return sha256(material.encode()).hexdigest()[:16]

    def get_or_create_promotion_code(self, *, coupon, billing_interval):
        """Return a Stripe promotion-code id for a local coupon.

        The local coupon's ``metadata`` caches the created Stripe objects so
        repeated checkouts do not create duplicates. Stripe promotion codes
        are immutable once created, so a discount that changed afterwards is
        rejected instead of silently charging the wrong amount.
        """
        discount = coupon.discount
        code = coupon.code.strip().upper()
        cached = (coupon.metadata or {}).get("stripe_promotion_code") or {}
        fingerprint = self._promotion_fingerprint(coupon, billing_interval)

        if cached.get("promotion_code_id"):
            if cached.get("fingerprint") != fingerprint:
                raise StripeConfigurationError(
                    "The coupon's discount changed after its Stripe promotion "
                    "code was created. Create a new coupon code instead."
                )
            if cached.get("code") == code:
                try:
                    promo = self._client().v1.promotion_codes.retrieve(
                        cached["promotion_code_id"]
                    )
                    if promo.get("active") and (
                        (promo.get("code") or "").upper() == code
                    ):
                        return cached["promotion_code_id"]
                except stripe.StripeError:
                    logger.warning(
                        "Cached Stripe promotion code %s could not be "
                        "retrieved; recreating it.",
                        cached["promotion_code_id"],
                    )

        coupon_params = {
            "name": f"{discount.name} ({code})"[:40],
            "duration": {
                DiscountDuration.ONCE: "once",
                DiscountDuration.REPEATING: "repeating",
                DiscountDuration.FOREVER: "forever",
            }[discount.duration],
            "metadata": {
                "argon_coupon_id": str(coupon.id),
                "argon_code": code,
            },
        }
        if discount.discount_type == DiscountType.PERCENTAGE:
            coupon_params["percent_off"] = float(discount.value)
        else:
            coupon_params["amount_off"] = stripe_minor_unit_amount(
                discount.value,
                discount.currency,
            )
            coupon_params["currency"] = discount.currency.strip().lower()
        if discount.duration == DiscountDuration.REPEATING:
            coupon_params["duration_in_months"] = (
                discount.duration_in_billing_cycles
                * (12 if billing_interval == BillingInterval.ANNUAL else 1)
            )
        if coupon.max_redemptions is not None:
            coupon_params["max_redemptions"] = coupon.max_redemptions
        if coupon.valid_until is not None:
            redeem_by = int(coupon.valid_until.timestamp())
            if redeem_by > int(time()):
                coupon_params["redeem_by"] = redeem_by

        promotion_params = {"code": code, "active": True}
        if coupon.minimum_purchase_amount is not None:
            promotion_params["restrictions"] = {
                "minimum_amount": stripe_minor_unit_amount(
                    coupon.minimum_purchase_amount,
                    coupon.minimum_purchase_currency,
                ),
                "minimum_amount_currency": (
                    coupon.minimum_purchase_currency.strip().lower()
                ),
            }

        client = self._client()
        try:
            stripe_coupon = client.v1.coupons.create(
                coupon_params,
                options={"idempotency_key": f"coupon-{coupon.id}-{fingerprint}"},
            )
            promotion_params["coupon"] = stripe_object_to_dict(stripe_coupon)[
                "id"
            ]
            promotion = client.v1.promotion_codes.create(
                promotion_params,
                options={
                    "idempotency_key": f"promo-{coupon.id}-{fingerprint}"
                },
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe promotion code creation failed")
            raise StripeServiceError(
                "Stripe could not prepare this coupon."
            ) from exc

        promotion_dict = stripe_object_to_dict(promotion)
        coupon.metadata = {
            **(coupon.metadata or {}),
            "stripe_promotion_code": {
                "code": code,
                "coupon_id": promotion_dict.get("coupon", "") or "",
                "promotion_code_id": promotion_dict.get("id", ""),
                "fingerprint": fingerprint,
            },
        }
        coupon.save(update_fields=["metadata", "updated_at"])
        return promotion_dict["id"]

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

    def create_setup_intent(self, *, customer_id, metadata):
        try:
            setup_intent = self._client().v1.setup_intents.create(
                {
                    "customer": customer_id,
                    "payment_method_types": ["card"],
                    "usage": "off_session",
                    "metadata": metadata,
                }
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe SetupIntent creation failed")
            raise StripeServiceError(
                "Stripe could not prepare a new payment method."
            ) from exc
        return stripe_object_to_dict(setup_intent)

    def list_card_payment_methods(self, *, customer_id):
        try:
            methods = self._client().v1.payment_methods.list(
                {"customer": customer_id, "type": "card"}
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe payment-method listing failed")
            raise StripeServiceError(
                "Stripe payment methods are temporarily unavailable."
            ) from exc
        return stripe_object_to_dict(methods).get("data", [])

    def retrieve_payment_method(self, *, payment_method_id):
        try:
            payment_method = self._client().v1.payment_methods.retrieve(
                payment_method_id
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe payment-method retrieval failed")
            raise StripeServiceError(
                "Stripe could not retrieve that payment method."
            ) from exc
        return stripe_object_to_dict(payment_method)

    def retrieve_customer(self, *, customer_id):
        try:
            customer = self._client().v1.customers.retrieve(customer_id)
        except stripe.StripeError as exc:
            logger.exception("Stripe customer retrieval failed")
            raise StripeServiceError(
                "Stripe billing details are temporarily unavailable."
            ) from exc
        return stripe_object_to_dict(customer)

    def retrieve_subscription(
        self, *, subscription_id, expand_latest_invoice=False
    ):
        try:
            params = {"expand": ["latest_invoice"]} if expand_latest_invoice else None
            if params is None:
                subscription = self._client().v1.subscriptions.retrieve(
                    subscription_id
                )
            else:
                subscription = self._client().v1.subscriptions.retrieve(
                    subscription_id, params
                )
        except stripe.StripeError as exc:
            logger.exception("Stripe subscription retrieval failed")
            raise StripeServiceError(
                "Stripe subscription details are temporarily unavailable."
            ) from exc
        return stripe_object_to_dict(subscription)

    def set_default_payment_method(
        self, *, customer_id, subscription_id, payment_method_id
    ):
        client = self._client().v1
        try:
            customer = client.customers.update(
                customer_id,
                {"invoice_settings": {"default_payment_method": payment_method_id}},
            )
            subscription = client.subscriptions.update(
                subscription_id,
                {"default_payment_method": payment_method_id},
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe default payment-method update failed")
            raise StripeServiceError(
                "Stripe could not update the default payment method."
            ) from exc
        return (
            stripe_object_to_dict(customer),
            stripe_object_to_dict(subscription),
        )

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
