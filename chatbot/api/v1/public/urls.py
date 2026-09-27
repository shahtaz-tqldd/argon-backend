from django.urls import path

from chatbot.api.v1.public import views

urlpatterns = [
    path("", views.PublicChatbotView.as_view(), name="public-chatbot"),
    path(
        "visitors/<str:visitor_id>/",
        views.PublicVisitorDetailView.as_view(),
        name="public-visitor-detail",
    ),
]
