from django.urls import path

from analytics.api.v1.admin.views import (
    AIUsageStatsAPIView,
    UserGrowthAPIView,
)


urlpatterns = [
    path("ai-usage/", AIUsageStatsAPIView.as_view(), name="analytics-ai-usage"),
    path("user-growth/", UserGrowthAPIView.as_view(), name="analytics-user-growth"),
]
