from django.urls import path

from subscription.api.v1.client import views


urlpatterns = [
    path("plans/", views.SubscriptionPlanListAPIView.as_view(), name="subscription-plan-list"),
    path("plans/details/", views.SubscriptionPlanDetailAPIView.as_view(), name="subscription-plan-details"),
    
    path("current/", views.CurrentSubscriptionAPIView.as_view(), name="current-subscription"),
    path("activate-free/", views.FreeSubscriptionAPIView.as_view(), name="subscription-activate-free"),
    path("cancellation/", views.SubscriptionCancellationAPIView.as_view(), name="subscription-cancellation"),
    
    path("billing-portal/", views.StripeBillingPortalAPIView.as_view(), name="stripe-billing-portal"),
    path("payment-methods/", views.StripePaymentMethodListAPIView.as_view(), name="stripe-payment-method-list"),
    path("payment-methods/setup/", views.StripePaymentMethodSetupAPIView.as_view(), name="stripe-payment-method-setup"),
    path("payment-methods/default/", views.StripeDefaultPaymentMethodAPIView.as_view(), name="stripe-payment-method-default"),
    path("auto-renewal/", views.StripeAutoRenewalAPIView.as_view(), name="stripe-auto-renewal"),
    path("checkout/", views.StripeCheckoutAPIView.as_view(), name="subscription-checkout"),
    path("stripe/webhook/", views.StripeWebhookAPIView.as_view(), name="stripe-webhook"),
    
    path("payments/", views.SubscriptionPaymentListAPIView.as_view(), name="subscription-payment-list"),
]
