from django.urls import path

from app.api.v1.views import TourtoiseConfigAPIView, TourtoiseConfigUpdateAPIView


client_urlpatterns = [
    path("", TourtoiseConfigAPIView.as_view(), name="tourtoise-config"),
]

admin_urlpatterns = [
    path("update/", TourtoiseConfigUpdateAPIView.as_view(), name="tourtoise-config-update"),
]
