from django.urls import path

from analytics.api.v1.admin.dashboard import (
    AIUsageAnalyticsAPIView,
    ConversationAnalyticsAPIView,
    GrowthAnalyticsAPIView,
    PlatformAnalyticsAPIView,
)


urlpatterns = [
    path(
        "ai-usage/",
        AIUsageAnalyticsAPIView.as_view(),
        name="analytics-ai-usage",
    ),
    path(
        "platform/",
        PlatformAnalyticsAPIView.as_view(),
        name="analytics-platform",
    ),
    path("growth/", GrowthAnalyticsAPIView.as_view(), name="analytics-growth"),
    path(
        "conversations/",
        ConversationAnalyticsAPIView.as_view(),
        name="analytics-conversations",
    ),
]
