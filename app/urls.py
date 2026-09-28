from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings

v1_client_urls = [
    path("base/", include("base.api.v1.client.urls")),
    path("accounts/", include("accounts.api.v1.client.urls")),
    path("workspaces/", include("workspace.api.v1.client.urls")),
    path("chatbots/", include("chatbot.api.v1.client.urls")),
    path("chat/", include("chat.api.v1.client.urls")),
    path("knowledge/", include("knowledge.api.v1.client.urls")),
    path("lead-captures/", include("lead_capture.api.v1.client.urls")),
    path("appointments/", include("appointment.api.v1.client.urls")),
    path("subscriptions/", include("subscription.api.v1.client.urls")),
    path("notifications/", include("notification.api.v1.client.urls")),
]

v1_admin_urls = [
    path("accounts/", include("accounts.api.v1.admin.urls")),
    path("analytics/", include("analytics.api.v1.admin.urls")),
    path("base/", include("base.api.v1.admin.urls")),
    path("chatbots/", include("chatbot.api.v1.admin.urls")),
    path("workspaces/", include("workspace.api.v1.admin.urls")),
    path("subscriptions/", include("subscription.api.v1.admin.urls")),
    # path("vector-store/", include("vector_store.api.v1.admin.urls")),
]

v1_public_urls = [
    path(
        "chatbots/<str:public_key>/",
        include("chatbot.api.v1.public.urls"),
    ),
    path(
        "chatbots/<str:public_key>/",
        include("chat.api.v1.public.urls"),
    ),
    path(
        "chatbots/<str:public_key>/",
        include("appointment.api.v1.public.urls"),
    ),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(v1_client_urls)),
    path("api/v1/admin/", include(v1_admin_urls)),
    path("api/v1/", include(v1_public_urls)),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
