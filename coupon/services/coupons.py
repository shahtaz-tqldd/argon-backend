from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from coupon.utils.choices import DiscountType
from coupon.models import Coupon, CouponRedemption
from subscription.utils.choices import PaymentStatus
from subscription.models import ChatbotSubscription

CENT = Decimal("0.01")


class CouponError(Exception):
    """Base class for user-safe coupon failures."""


class CouponNotFoundError(CouponError):
    """Raised when no coupon exists for the requested code."""


class CouponNotEligibleError(CouponError):
    """Raised when a coupon exists but cannot be applied."""


def get_coupon_by_code(code):
    code = (code or "").strip().upper()
    coupon = (
        Coupon.objects.select_related("discount")
        .filter(code__iexact=code)
        .first()
    )
    if coupon is None:
        raise CouponNotFoundError("No coupon exists for this code.")
    return coupon


def compute_discounted_amounts(discount, amount, currency):
    """Return ``(original_amount, discount_amount, final_amount)`` for a price."""
    original = Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)
    currency = (currency or "").strip().upper()

    if discount.discount_type == DiscountType.PERCENTAGE:
        raw = original * Decimal(discount.value) / Decimal("100")
    else:
        if (discount.currency or "").upper() != currency:
            raise CouponNotEligibleError(
                "This coupon can only be used for prices in "
                f"{discount.currency}."
            )
        raw = Decimal(discount.value)

    discount_amount = min(
        raw.quantize(CENT, rounding=ROUND_HALF_UP),
        original,
    )
    return original, discount_amount, original - discount_amount


def _succeeded_payment_exists_for_chatbot(chatbot):
    from subscription.models import Payment

    if chatbot is None:
        return False
    return Payment.objects.filter(
        subscription__chatbot=chatbot,
        status=PaymentStatus.SUCCEEDED,
    ).exists()


def validate_coupon(
    coupon,
    *,
    user,
    plan_price,
    chatbot=None,
    subscription=None,
):
    """Raise ``CouponNotEligibleError`` when the coupon cannot be applied."""
    now = timezone.now()
    discount = coupon.discount

    if not coupon.is_active or not discount.is_active:
        raise CouponNotEligibleError("This coupon is no longer available.")
    if now < coupon.valid_from:
        raise CouponNotEligibleError("This coupon is not active yet.")
    if coupon.valid_until is not None and now > coupon.valid_until:
        raise CouponNotEligibleError("This coupon has expired.")

    if Decimal(plan_price.amount) == 0:
        raise CouponNotEligibleError(
            "Coupons cannot be applied to free plans."
        )

    if not coupon.applies_to_all_plans and not coupon.eligible_plans.filter(
        pk=plan_price.plan_id
    ).exists():
        raise CouponNotEligibleError(
            "This coupon is not valid for the selected plan."
        )

    currency = (plan_price.currency or "").strip().upper()
    if discount.discount_type == DiscountType.FIXED_AMOUNT and (
        discount.currency or "").upper() != currency:
        raise CouponNotEligibleError(
            f"This coupon can only be used for prices in "
            f"{discount.currency}."
        )

    if coupon.minimum_purchase_amount is not None:
        if (coupon.minimum_purchase_currency or "").upper() != currency:
            raise CouponNotEligibleError(
                "This coupon requires a minimum purchase in "
                f"{coupon.minimum_purchase_currency}."
            )
        if Decimal(plan_price.amount) < Decimal(coupon.minimum_purchase_amount):
            raise CouponNotEligibleError(
                "This coupon requires a minimum purchase of "
                f"{coupon.minimum_purchase_amount} "
                f"{coupon.minimum_purchase_currency}."
            )

    redemption_counts = CouponRedemption.objects.filter(coupon=coupon)
    if coupon.max_redemptions is not None and (
        redemption_counts.count() >= coupon.max_redemptions
    ):
        raise CouponNotEligibleError(
            "This coupon has reached its redemption limit."
        )
    if (
        user is not None
        and coupon.max_redemptions_per_user is not None
        and redemption_counts.filter(user=user).count()
        >= coupon.max_redemptions_per_user
    ):
        raise CouponNotEligibleError(
            "You have already used this coupon the maximum number of times."
        )

    if coupon.first_payment_only and _succeeded_payment_exists_for_chatbot(
        chatbot
    ):
        raise CouponNotEligibleError(
            "This coupon is only valid for a chatbot's first purchase."
        )

    if subscription is not None and CouponRedemption.objects.filter(
        subscription=subscription,
        is_active=True,
    ).exists():
        raise CouponNotEligibleError(
            "This subscription already has an active coupon."
        )


def coupon_preview(coupon, plan_price):
    """Return a serializable preview of a coupon applied to one price."""
    discount = coupon.discount
    original, discount_amount, final = compute_discounted_amounts(
        discount,
        plan_price.amount,
        plan_price.currency,
    )
    return {
        "coupon": {
            "id": str(coupon.id),
            "code": coupon.code,
            "name": coupon.name,
            "discount_type": discount.discount_type,
            "value": str(discount.value),
            "currency": discount.currency,
            "duration": discount.duration,
            "duration_in_billing_cycles": discount.duration_in_billing_cycles,
            "valid_until": coupon.valid_until,
            "max_redemptions": coupon.max_redemptions,
            "max_redemptions_per_user": coupon.max_redemptions_per_user,
        },
        "plan_price": {
            "id": str(plan_price.id),
            "plan_id": str(plan_price.plan_id),
            "billing_interval": plan_price.billing_interval,
        },
        "currency": (plan_price.currency or "").strip().upper(),
        "original_amount": str(original),
        "discount_amount": str(discount_amount),
        "final_amount": str(final),
    }


def pending_coupon_info(coupon):
    discount = coupon.discount
    return {
        "id": str(coupon.id),
        "code": coupon.code,
        "name": coupon.name,
        "discount_type": discount.discount_type,
        "value": str(discount.value),
        "currency": discount.currency,
        "duration": discount.duration,
        "duration_in_billing_cycles": discount.duration_in_billing_cycles,
    }


def get_pending_coupon(subscription):
    """Return the ``Coupon`` bound to a checkout, or ``None``."""
    if subscription is None:
        return None
    pending = (subscription.provider_metadata or {}).get("pending_coupon") or {}
    coupon_id = pending.get("id")
    if not coupon_id:
        return None
    return (
        Coupon.objects.select_related("discount")
        .filter(pk=coupon_id, is_active=True)
        .first()
    )


def bind_pending_coupon(subscription, *, coupon, user, chatbot, plan_price):
    """Attach a validated coupon to a subscription's next checkout."""
    validate_coupon(
        coupon,
        user=user,
        plan_price=plan_price,
        chatbot=chatbot,
        subscription=subscription,
    )
    info = pending_coupon_info(coupon)
    with transaction.atomic():
        locked = ChatbotSubscription.objects.select_for_update().get(
            pk=subscription.pk
        )
        locked.provider_metadata = {
            **(locked.provider_metadata or {}),
            "pending_coupon": info,
        }
        locked.updated_by = user
        locked.save(
            update_fields=["provider_metadata", "updated_by", "updated_at"]
        )
        return locked


def bind_pending_coupon_by_code(
    subscription, *, code, user, chatbot, plan_price
):
    coupon = get_coupon_by_code(code)
    return bind_pending_coupon(
        subscription,
        coupon=coupon,
        user=user,
        chatbot=chatbot,
        plan_price=plan_price,
    )


def clear_pending_coupon(subscription, user=None):
    """Remove the pending coupon binding so the next checkout is undiscounted."""
    with transaction.atomic():
        locked = ChatbotSubscription.objects.select_for_update().get(
            pk=subscription.pk
        )
        metadata = dict(locked.provider_metadata or {})
        removed = metadata.pop("pending_coupon", None)
        # Keep any stale checkout_coupon_id so an open discounted Stripe
        # Session is rotated instead of reused.
        locked.provider_metadata = metadata
        locked.updated_by = user
        locked.save(
            update_fields=["provider_metadata", "updated_by", "updated_at"]
        )
        return removed


def eligible_coupon_filters(now=None):
    now = now or timezone.now()
    return Q(is_active=True, valid_from__lte=now) & (
        Q(valid_until__isnull=True) | Q(valid_until__gte=now)
    )
