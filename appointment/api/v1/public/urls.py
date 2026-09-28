from django.urls import path

from appointment.api.v1.public import views


urlpatterns = [
    path("book-appointment/", views.VisitorAppointmentCreateAPIView.as_view(), name="book-appointment"),
]
