from django.urls import path

from subscription.api.v1.admin.view import (
    SubscriptionPlanCreateAPIView,
    SubscriptionPlanDeleteAPIView,
    SubscriptionPlanUpdateAPIView,
)


urlpatterns = [
    path(
        "plans/create/",
        SubscriptionPlanCreateAPIView.as_view(),
        name="subscription-plan-create",
    ),
    path(
        "plans/<uuid:plan_id>/update/",
        SubscriptionPlanUpdateAPIView.as_view(),
        name="subscription-plan-update",
    ),
    path(
        "plans/<uuid:plan_id>/delete/",
        SubscriptionPlanDeleteAPIView.as_view(),
        name="subscription-plan-delete",
    ),
]
