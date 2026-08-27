import logging

from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from chatbot.models import Chatbot
from subscription.api.v1.client.serializers import (
    ChatbotSubscriptionClientSerializer,
    FreeSubscriptionSerializer,
    PaymentClientSerializer,
    StripeCheckoutSerializer,
    SubscriptionCancellationSerializer,
    SubscriptionChatbotQuerySerializer,
    SubscriptionPlanClientSerializer,
    SubscriptionPlanQuerySerializer,
)
from subscription.choices import PaymentProvider, SubscriptionStatus
from subscription.models import ChatbotSubscription, Payment, PlanPrice, SubscriptionPlan
from subscription.services.stripe import (
    StripeBillingService,
    StripeConfigurationError,
    StripePaymentRequiredError,
    StripeServiceError,
    StripeWebhookError,
)
from subscription.services.subscriptions import (
    SubscriptionConflictError,
    activate_free_subscription,
    get_open_subscription,
    start_stripe_checkout,
)
from subscription.services.webhooks import StripeWebhookProcessor


logger = logging.getLogger("app.subscription.api")


def _available_plan_queryset():
    available_prices = (
        PlanPrice.objects.filter(is_active=True)
        .filter(
            Q(provider=PaymentProvider.STRIPE) | Q(plan__is_free=True)
        )
        .order_by("billing_interval", "currency")
    )
    return SubscriptionPlan.objects.filter(
        is_active=True,
        is_public=True,
    ).prefetch_related(
        Prefetch(
            "prices",
            queryset=available_prices,
            to_attr="available_prices",
        )
    )


class SubscriptionChatbotMixin:
    _chatbot = None

    def get_chatbot(self):
        if self._chatbot is None:
            query_serializer = SubscriptionChatbotQuerySerializer(
                data=self.request.query_params
            )
            query_serializer.is_valid(raise_exception=True)
            self._chatbot = get_object_or_404(
                Chatbot.objects.select_related("workspace").filter(
                    is_deleted=False,
                    workspace__is_active=True,
                ),
                slug=query_serializer.validated_data["chatbot"],
            )
            self.check_object_permissions(self.request, self._chatbot)
        return self._chatbot


class SubscriptionPlanListAPIView(GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = SubscriptionPlanClientSerializer

    def get(self, request, *args, **kwargs):
        plans = _available_plan_queryset()
        return APIResponse.success(
            data=self.get_serializer(plans, many=True).data,
            meta={"count": plans.count()},
            message="Subscription plans fetched successfully.",
        )


class SubscriptionPlanDetailAPIView(GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = SubscriptionPlanClientSerializer

    def get(self, request, *args, **kwargs):
        query_serializer = SubscriptionPlanQuerySerializer(
            data=request.query_params
        )
        query_serializer.is_valid(raise_exception=True)
        plan = get_object_or_404(
            _available_plan_queryset(),
            slug=query_serializer.validated_data["plan"],
        )
        return APIResponse.success(
            data=self.get_serializer(plan).data,
            message="Subscription plan fetched successfully.",
        )


class StripeCheckoutAPIView(SubscriptionChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = StripeCheckoutSerializer
    chatbot_admin_only = True

    def post(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            checkout_result = start_stripe_checkout(
                chatbot=chatbot,
                plan_price=serializer.validated_data["plan_price"],
                user=request.user,
            )
        except SubscriptionConflictError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_409_CONFLICT,
            )
        except StripeConfigurationError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except StripePaymentRequiredError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        except StripeServiceError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return APIResponse.success(
            data={
                "subscription_id": str(checkout_result.subscription.id),
                "subscription_status": checkout_result.subscription.status,
                "client_secret": checkout_result.client_secret,
                "requires_checkout": bool(checkout_result.client_secret),
                "reused": checkout_result.reused,
                "action": checkout_result.action,
            },
            message={
                "checkout": "Stripe checkout is ready.",
                "subscription_activated": (
                    "Stripe payment confirmed and subscription activated."
                ),
                "plan_changed": "Subscription plan changed successfully.",
                "already_active": "This subscription plan is already active.",
            }[checkout_result.action],
            status=(
                status.HTTP_201_CREATED
                if checkout_result.action == "checkout"
                and not checkout_result.reused
                else status.HTTP_200_OK
            ),
        )


class FreeSubscriptionAPIView(SubscriptionChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = FreeSubscriptionSerializer
    chatbot_admin_only = True

    def post(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            subscription, created = activate_free_subscription(
                chatbot=chatbot,
                plan_price=serializer.validated_data["plan_price"],
                user=request.user,
            )
        except SubscriptionConflictError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_409_CONFLICT,
            )
        return APIResponse.success(
            data=ChatbotSubscriptionClientSerializer(subscription).data,
            message="Free subscription activated successfully.",
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class CurrentSubscriptionAPIView(SubscriptionChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = ChatbotSubscriptionClientSerializer

    def get(self, request, *args, **kwargs):
        subscription = get_open_subscription(self.get_chatbot())
        data = self.get_serializer(subscription).data if subscription else None
        return APIResponse.success(
            data={"subscription": data},
            message="Current subscription fetched successfully.",
        )


class SubscriptionPaymentListAPIView(SubscriptionChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = PaymentClientSerializer
    pagination_class = CustomPagination
    chatbot_admin_only = True

    def get(self, request, *args, **kwargs):
        queryset = Payment.objects.filter(
            subscription__chatbot=self.get_chatbot(),
        ).select_related("subscription", "plan_price")
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return APIResponse.success(
            data=self.get_serializer(page, many=True).data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Subscription payments fetched successfully.",
        )


class StripeBillingPortalAPIView(SubscriptionChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    chatbot_admin_only = True

    def post(self, request, *args, **kwargs):
        subscription = get_open_subscription(self.get_chatbot())
        if (
            subscription is None
            or subscription.provider != PaymentProvider.STRIPE
            or not subscription.provider_customer_id
        ):
            return APIResponse.error(
                message="This chatbot has no Stripe billing account.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            portal = StripeBillingService().create_portal_session(
                customer_id=subscription.provider_customer_id
            )
        except StripeConfigurationError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except StripeServiceError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return APIResponse.success(
            data={"portal_url": portal["url"]},
            message="Stripe billing portal is ready.",
        )


class SubscriptionCancellationAPIView(SubscriptionChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = SubscriptionCancellationSerializer
    chatbot_admin_only = True

    def post(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subscription = get_open_subscription(chatbot)
        if subscription is not None and subscription.is_free_plan():
            now = timezone.now()
            subscription.status = SubscriptionStatus.CANCELED
            subscription.canceled_at = now
            subscription.ended_at = now
            subscription.updated_by = request.user
            subscription.save(
                update_fields=[
                    "status",
                    "canceled_at",
                    "ended_at",
                    "updated_by",
                    "updated_at",
                ]
            )
            return APIResponse.success(
                data=ChatbotSubscriptionClientSerializer(subscription).data,
                message="Free subscription canceled successfully.",
            )
        if (
            subscription is None
            or subscription.provider != PaymentProvider.STRIPE
            or not subscription.provider_subscription_id
        ):
            return APIResponse.error(
                message="This chatbot has no manageable Stripe subscription.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        cancel = serializer.validated_data["cancel_at_period_end"]
        try:
            stripe_subscription = StripeBillingService().set_cancel_at_period_end(
                subscription_id=subscription.provider_subscription_id,
                cancel=cancel,
            )
        except StripeConfigurationError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except StripeServiceError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_502_BAD_GATEWAY,
            )

        subscription.cancel_at_period_end = bool(
            stripe_subscription.get("cancel_at_period_end", cancel)
        )
        subscription.updated_by = request.user
        subscription.save(
            update_fields=[
                "cancel_at_period_end",
                "updated_by",
                "updated_at",
            ]
        )
        return APIResponse.success(
            data=ChatbotSubscriptionClientSerializer(subscription).data,
            message=(
                "Subscription will cancel at the end of the billing period."
                if subscription.cancel_at_period_end
                else "Scheduled subscription cancellation removed."
            ),
        )


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        try:
            webhook_event, duplicate = StripeWebhookProcessor().process(
                payload=request.body,
                signature=request.headers.get("Stripe-Signature", ""),
            )
        except StripeWebhookError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except StripeConfigurationError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Stripe webhook delivery could not be processed")
            return APIResponse.error(
                message="Stripe webhook processing failed.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return APIResponse.success(
            data={
                "event_id": webhook_event.provider_event_id,
                "duplicate": duplicate,
            },
            message="Stripe webhook accepted.",
        )
