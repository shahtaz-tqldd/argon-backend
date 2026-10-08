from rest_framework import status
from rest_framework.generics import GenericAPIView

from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from coupon.api.v1.client.serializers import (
    CouponApplySerializer,
    CouponRedemptionClientSerializer,
)
from coupon.services import (
    CouponNotEligibleError,
    CouponNotFoundError,
    bind_pending_coupon,
    clear_pending_coupon,
    coupon_preview,
    get_coupon_by_code,
    get_pending_coupon,
    validate_coupon,
)
from subscription.api.v1.client.views import SubscriptionChatbotMixin
from subscription.utils.choices import SubscriptionStatus
from subscription.services.subscriptions import get_open_subscription


class CouponApplyAPIView(SubscriptionChatbotMixin, GenericAPIView):
    """Validate a coupon code and bind it to the chatbot's next checkout."""

    permission_classes = [IsChatbotUser]
    serializer_class = CouponApplySerializer
    chatbot_admin_only = True

    def post(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan_price = serializer.validated_data.get("plan_price")
        subscription = get_open_subscription(chatbot)

        if plan_price is None:
            if subscription is None:
                return APIResponse.error(
                    errors={
                        "plan_price_id": (
                            "A plan_price_id is required when the chatbot has "
                            "no open subscription."
                        )
                    },
                    message="A plan price is required to apply this coupon.",
                    status=status.HTTP_400_BAD_REQUEST,
                )
            plan_price = subscription.plan_price

        try:
            coupon = get_coupon_by_code(serializer.validated_data["code"])
            validate_coupon(
                coupon,
                user=request.user,
                plan_price=plan_price,
                chatbot=chatbot,
                subscription=subscription,
            )
        except CouponNotFoundError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_404_NOT_FOUND,
            )
        except CouponNotEligibleError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_400_BAD_REQUEST,
            )

        bound = False
        if (
            subscription is not None
            and subscription.status == SubscriptionStatus.INCOMPLETE
        ):
            subscription = bind_pending_coupon(
                subscription,
                coupon=coupon,
                user=request.user,
                chatbot=chatbot,
                plan_price=plan_price,
            )
            bound = True

        preview = coupon_preview(coupon, plan_price)
        preview["bound"] = bound
        return APIResponse.success(
            data=preview,
            message=(
                "Coupon applied. It will be used on the next checkout."
                if bound
                else "Coupon is valid. Pass coupon_code when starting "
                "checkout to use it."
            ),
        )


class CouponCurrentAPIView(SubscriptionChatbotMixin, GenericAPIView):
    """Show the pending checkout coupon and the active redeemed coupon."""

    permission_classes = [IsChatbotUser]
    chatbot_admin_only = True

    def get(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        subscription = get_open_subscription(chatbot)

        pending_coupon = (
            get_pending_coupon(subscription) if subscription is not None else None
        )
        pending = None
        if pending_coupon is not None:
            pending = coupon_preview(pending_coupon, subscription.plan_price)

        redemption = None
        if subscription is not None:
            redemption = (
                subscription.coupon_redemptions.select_related(
                    "coupon", "coupon__discount"
                )
                .filter(is_active=True)
                .order_by("-redeemed_at")
                .first()
            )

        return APIResponse.success(
            data={
                "chatbot": chatbot.slug,
                "pending_coupon": pending,
                "coupon_redemption": (
                    CouponRedemptionClientSerializer(redemption).data
                    if redemption is not None
                    else None
                ),
            },
            message="Coupon status fetched successfully.",
        )


class CouponRemoveAPIView(SubscriptionChatbotMixin, GenericAPIView):
    """Remove the pending coupon so the next checkout is undiscounted."""

    permission_classes = [IsChatbotUser]
    chatbot_admin_only = True

    def post(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        subscription = get_open_subscription(chatbot)
        if subscription is None:
            return APIResponse.error(
                message="This chatbot has no open subscription.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        removed = clear_pending_coupon(subscription, user=request.user)
        return APIResponse.success(
            data={"removed": bool(removed)},
            message=(
                "Coupon removed. The next checkout will not use it."
                if removed
                else "No coupon was applied to this subscription."
            ),
        )
