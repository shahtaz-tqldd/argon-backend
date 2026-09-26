from django.urls import path

from chatbot.api.v1.admin import views

# chatbot
urlpatterns = [
    path("list/", views.AdminChatbotListAPIView.as_view(), name="chatbot-list"),
    path("details/", views.AdminChatbotDetailView.as_view(), name="chatbot-detail"),
    path("update/", views.AdminChatbotUpdateView.as_view(), name="chatbot-update"),
    path("delete/", views.AdminChatbotDeleteView.as_view(), name="chatbot-delete"),
]
