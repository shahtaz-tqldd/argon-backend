from django.urls import include, path

from appointment_booking.api.v1.client import views


config_patterns = [
    path(
        "",
        views.AppointmentBookingConfigView.as_view(),
        name="appointment-booking-config",
    ),
    path(
        "update/",
        views.AppointmentBookingConfigUpdateView.as_view(),
        name="appointment-booking-config-update",
    ),
]

schedule_patterns = [
    path(
        "",
        views.AppointmentBookingScheduleView.as_view(),
        name="appointment-booking-schedules",
    ),
    path(
        "update/",
        views.AppointmentBookingScheduleUpdateView.as_view(),
        name="appointment-booking-schedules-update",
    ),
]

appointment_patterns = [
    path("list/", views.AppointmentListView.as_view(), name="appointment-list"),
    path(
        "update/",
        views.AppointmentUpdateView.as_view(),
        name="appointment-update",
    ),
    path(
        "delete/",
        views.AppointmentDeleteView.as_view(),
        name="appointment-delete",
    ),
]

urlpatterns = [
    path("config/", include(config_patterns)),
    path("schedules/", include(schedule_patterns)),
    path("appointments/", include(appointment_patterns)),
]
