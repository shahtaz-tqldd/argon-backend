from django.urls import path

from analytics.api.v1.admin.views import (
    AIUsageStatsAPIView,
    OverviewStatsAPIView,
    UserGrowthAPIView,
)


urlpatterns = [
    path("overview/", OverviewStatsAPIView.as_view(), name="analytics-overview"),
    path("ai-usage/", AIUsageStatsAPIView.as_view(), name="analytics-ai-usage"),
    path("user-growth/", UserGrowthAPIView.as_view(), name="analytics-user-growth"),
]
