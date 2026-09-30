from django.urls import path

from subscription.api.v1.admin import views

urlpatterns = [
    path("plans/create/", views.SubscriptionPlanCreateAPIView.as_view(), name="subscription-plan-create"),
    path("plans/<uuid:plan_id>/update/", views.SubscriptionPlanUpdateAPIView.as_view(), name="subscription-plan-update"),
    path("plans/<uuid:plan_id>/delete/", views.SubscriptionPlanDeleteAPIView.as_view(), name="subscription-plan-delete"),
]
