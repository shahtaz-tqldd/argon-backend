from django.urls import path

from appointment_booking.api.v1.public import views


urlpatterns = [
    path(
        "conversations/<uuid:session_id>/appointments/",
        views.VisitorAppointmentCreateView.as_view(),
        name="visitor-appointment-create",
    ),
]
