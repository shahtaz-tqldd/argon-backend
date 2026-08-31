from django.db import models


class AppointmentFieldMode(models.TextChoices):
    HIDDEN = "hidden", "Hidden"
    OPTIONAL = "optional", "Optional"
    REQUIRED = "required", "Required"


class AppointmentFieldType(models.TextChoices):
    TEXT = "text", "Text"
    EMAIL = "email", "Email"
    DATE = "date", "Date"


class AppointmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    NO_SHOW = "no_show", "No show"


class Weekday(models.IntegerChoices):
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"
