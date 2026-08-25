from django.urls import path

from subscription.api.v1.client.views import (
    CurrentSubscriptionAPIView,
    FreeSubscriptionAPIView,
    StripeBillingPortalAPIView,
    StripeCheckoutAPIView,
    StripeWebhookAPIView,
    SubscriptionCancellationAPIView,
    SubscriptionPaymentListAPIView,
    SubscriptionPlanDetailAPIView,
    SubscriptionPlanListAPIView,
)


urlpatterns = [
    path("plans/", SubscriptionPlanListAPIView.as_view(), name="subscription-plan-list"),
    path(
        "plans/details/",
        SubscriptionPlanDetailAPIView.as_view(),
        name="subscription-plan-details",
    ),
    path("checkout/", StripeCheckoutAPIView.as_view(), name="subscription-checkout"),
    path(
        "activate-free/",
        FreeSubscriptionAPIView.as_view(),
        name="subscription-activate-free",
    ),
    path(
        "current/",
        CurrentSubscriptionAPIView.as_view(),
        name="current-subscription",
    ),
    path(
        "payments/",
        SubscriptionPaymentListAPIView.as_view(),
        name="subscription-payment-list",
    ),
    path(
        "billing-portal/",
        StripeBillingPortalAPIView.as_view(),
        name="stripe-billing-portal",
    ),
    path(
        "cancellation/",
        SubscriptionCancellationAPIView.as_view(),
        name="subscription-cancellation",
    ),
    path(
        "stripe/webhook/",
        StripeWebhookAPIView.as_view(),
        name="stripe-webhook",
    ),
]
