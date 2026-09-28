from django.urls import path, include

from chatbot.api.v1.public import views

visitor_apis = [
    path("details/", views.PublicVisitorDetailsAPIView.as_view(), name="public-visitor-details"),
    path("create/", views.PublicVisitorCreateAPIView.as_view(), name="public-visitor-create"),
]

urlpatterns = [
    path("config/", views.PublicChatbotConfigAPIView.as_view(), name="public-chatbot-config"),
    path("visitor/", include(visitor_apis)),
]
