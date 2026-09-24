from django.urls import include, path

from appointment_booking.api.v1.client import views


appointment_config = [
    path("", views.AppointmentBookingConfigView.as_view(), name="appointment-booking-config"),
    path("update/", views.AppointmentBookingConfigUpdateView.as_view(), name="appointment-booking-config-update"),
]

appointment_schedules = [
    path("", views.AppointmentBookingScheduleView.as_view(), name="appointment-booking-schedules"),
    path("update/", views.AppointmentBookingScheduleUpdateView.as_view(), name="appointment-booking-schedules-update"),
]

booked_appointments = [
    path("list/", views.AppointmentListView.as_view(), name="appointment-list"),
    path("update/", views.AppointmentUpdateView.as_view(), name="appointment-update"),
    path("delete/", views.AppointmentDeleteView.as_view(), name="appointment-delete"),
]

urlpatterns = [
    path("config/", include(appointment_config)),
    path("schedules/", include(appointment_schedules)),
    path("appointments/", include(booked_appointments)),
]
