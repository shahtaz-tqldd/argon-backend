from django.apps import AppConfig


class AppointmentAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "appointment"
    # Keep the historical Django app identity after renaming the Python package.
    # Existing migration records, content types, and permissions use this label.
    label = "appointment_booking"
    verbose_name = "Appointment"
