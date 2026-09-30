from django.db import transaction
from django.db.models import (
    Count,
    DecimalField,
    Min,
    Q,
    Sum,
    Max,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from coupon.choices import DiscountDuration
from coupon.models import CouponRedemption
from coupon.services.coupons import compute_discounted_amounts
from subscription.models import ChatbotSubscription


def _sum_or_zero(field):
    return Coalesce(Sum(field), 0, output_field=DecimalField())


def advance_redemption_billing_cycle(redemption):
    """Consume one discounted billing cycle after a later invoice is paid."""
    if redemption.discount_duration_snapshot == DiscountDuration.FOREVER:
        return redemption

    remaining = redemption.billing_cycles_remaining or 0
    if (
        redemption.discount_duration_snapshot == DiscountDuration.REPEATING
        and remaining > 1
    ):
        redemption.billing_cycles_remaining = remaining - 1
        redemption.save(
            update_fields=["billing_cycles_remaining", "updated_at"]
        )
        return redemption

    if redemption.is_active:
        redemption.is_active = False
        redemption.billing_cycles_remaining = 0
        redemption.save(
            update_fields=[
                "billing_cycles_remaining",
                "is_active",
                "updated_at",
            ]
        )
    return redemption


def _clear_pending_coupon_metadata(locked):
    metadata = dict(locked.provider_metadata or {})
    metadata.pop("pending_coupon", None)
    locked.provider_metadata = metadata
    locked.save(update_fields=["provider_metadata", "updated_at"])


def record_coupon_redemption_for_payment(subscription, payment):
    """Create or advance the redemption after a successful invoice payment.

    The first paid invoice converts the subscription's pending coupon into a
    redemption with an immutable discount snapshot. Every later paid invoice
    consumes one billing cycle of a repeating discount.
    """
    from coupon.models import Coupon

    with transaction.atomic():
        locked = ChatbotSubscription.objects.select_for_update().get(
            pk=subscription.pk
        )

        redemption = (
            CouponRedemption.objects.select_for_update()
            .filter(subscription=locked, is_active=True)
            .first()
        )
        if redemption is not None:
            if redemption.payment_id != payment.id:
                advance_redemption_billing_cycle(redemption)
            return redemption

        pending = (locked.provider_metadata or {}).get("pending_coupon") or {}
        coupon = (
            Coupon.objects.select_related("discount")
            .filter(pk=pending.get("id"), is_active=True)
            .first()
        )
        if coupon is None:
            if pending:
                _clear_pending_coupon_metadata(locked)
            return None

        amount = locked.get_price_amount()
        currency = locked.get_currency() or payment.currency
        if amount is None:
            amount = payment.amount
        original, discount_amount, final = compute_discounted_amounts(
            coupon.discount,
            amount,
            currency,
        )

        redemption = CouponRedemption.objects.create(
            coupon=coupon,
            subscription=locked,
            payment=payment,
            user=payment.user or locked.selected_by,
            original_amount=original,
            discount_amount=discount_amount,
            final_amount=final,
            currency=currency,
            is_active=True,
            redeemed_at=payment.paid_at or timezone.now(),
            created_by=locked.selected_by,
            updated_by=locked.selected_by,
        )
        _clear_pending_coupon_metadata(locked)
        return redemption


def coupon_redemptions_queryset(coupon, *, is_active=None, currency=None):
    queryset = (
        coupon.redemptions.select_related(
            "coupon",
            "coupon__discount",
            "user",
            "subscription",
            "subscription__chatbot",
            "payment",
        )
        .order_by("-redeemed_at")
    )
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    if currency:
        queryset = queryset.filter(
            currency__iexact=currency.strip().upper()
        )
    return queryset


def coupon_usage_summary(coupon):
    """Aggregate redemption usage for admin reporting."""
    redemptions = coupon.redemptions.all()
    totals = redemptions.aggregate(
        total_redemptions=Count("id"),
        active_redemptions=Count("id", filter=Q(is_active=True)),
        unique_users=Count("user", distinct=True),
        total_original_amount=_sum_or_zero("original_amount"),
        total_discount_amount=_sum_or_zero("discount_amount"),
        total_final_amount=_sum_or_zero("final_amount"),
        first_redeemed_at=Min("redeemed_at"),
        last_redeemed_at=Max("redeemed_at"),
    )
    per_currency = list(
        redemptions.values("currency")
        .annotate(
            redemptions=Count("id"),
            active_redemptions=Count("id", filter=Q(is_active=True)),
            original_amount=_sum_or_zero("original_amount"),
            discount_amount=_sum_or_zero("discount_amount"),
            final_amount=_sum_or_zero("final_amount"),
        )
        .order_by("currency")
    )
    for row in per_currency:
        row["original_amount"] = str(row["original_amount"])
        row["discount_amount"] = str(row["discount_amount"])
        row["final_amount"] = str(row["final_amount"])

    usage = dict(totals)
    usage["total_original_amount"] = str(usage["total_original_amount"])
    usage["total_discount_amount"] = str(usage["total_discount_amount"])
    usage["total_final_amount"] = str(usage["total_final_amount"])
    usage["remaining_redemptions"] = (
        None
        if coupon.max_redemptions is None
        else max(coupon.max_redemptions - usage["total_redemptions"], 0)
    )
    usage["per_currency"] = per_currency
    return usage
