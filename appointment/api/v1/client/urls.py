from django.urls import include, path

from appointment.api.v1.client import views


appointment_config = [
    path("", views.AppointmentBookingConfigAPIView.as_view(), name="appointment-booking-config"),
    path("update/", views.AppointmentBookingConfigUpdateAPIView.as_view(), name="appointment-booking-config-update"),
]

appointment_schedules = [
    path("", views.AppointmentBookingScheduleAPIView.as_view(), name="appointment-booking-schedules"),
    path("update/", views.AppointmentBookingScheduleUpdateAPIView.as_view(), name="appointment-booking-schedules-update"),
]

booked_appointments = [
    path("stats/", views.AppointmentStatsAPIView.as_view(), name="appointment-stats"),
    path("list/", views.AppointmentListAPIView.as_view(), name="appointment-list"),
    path("update/", views.AppointmentUpdateAPIView.as_view(), name="appointment-update"),
    path("delete/", views.AppointmentDeleteAPIView.as_view(), name="appointment-delete"),
]

urlpatterns = [
    path("config/", include(appointment_config)),
    path("schedules/", include(appointment_schedules)),
    path("appointments/", include(booked_appointments)),
]
