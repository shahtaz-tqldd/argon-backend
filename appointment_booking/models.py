from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.db.models import F, Q
from django.utils.dateparse import parse_date

from app.base.models import BaseModel
from appointment_booking.utils.choices import (
    AppointmentFieldMode,
    AppointmentStatus,
    Weekday,
)
from appointment_booking.utils.validators import (
    default_collectable_fields,
    validate_appointment_metadata,
    validate_collectable_fields,
    validate_collected_fields,
)


class AppointmentBookingConfig(BaseModel):
    """Appointment collection fields and booking rules for one chatbot."""

    chatbot = models.OneToOneField(
        "chatbot.Chatbot",
        on_delete=models.CASCADE,
        related_name="appointment_booking_config",
    )

    is_enabled = models.BooleanField(default=False)

    collectable_fields = models.JSONField(
        default=default_collectable_fields,
        validators=[validate_collectable_fields],
        help_text=(
            "The predefined name, email, phone, date, and slot fields as "
            "label/value/mode/type objects."
        ),
    )

    # Booking rules
    appointment_duration_minutes = models.PositiveSmallIntegerField(default=30)
    maximum_advance_days = models.PositiveSmallIntegerField(default=30)
    max_appointments_per_day = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Leave blank for no daily limit.",
    )
    confirmation_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "appointment_booking_config"
        verbose_name = "Appointment booking config"
        verbose_name_plural = "Appointment booking configs"
        constraints = [
            models.CheckConstraint(
                condition=Q(appointment_duration_minutes__gt=0),
                name="appt_config_duration_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(maximum_advance_days__gt=0),
                name="appt_config_advance_days_gt_0",
            ),
            models.CheckConstraint(
                condition=(
                    Q(max_appointments_per_day__isnull=True)
                    | Q(max_appointments_per_day__gt=0)
                ),
                name="appt_config_daily_limit_gt_0",
            ),
        ]

    def clean(self):
        super().clean()
        visible_fields = [
            field
            for field in (
                self.collectable_fields
                if isinstance(self.collectable_fields, list)
                else []
            )
            if isinstance(field, dict)
            and field.get("mode") != AppointmentFieldMode.HIDDEN
        ]
        if self.is_enabled and not visible_fields:
            raise ValidationError(
                "At least one booking field must be available when appointment "
                "booking is enabled."
            )

    def __str__(self):
        return f"Appointment booking: {self.chatbot}"


class AppointmentBookingSchedule(BaseModel):
    """One recurring weekday schedule for a booking config."""
    config = models.ForeignKey(
        AppointmentBookingConfig,
        on_delete=models.CASCADE,
        related_name="schedules",
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "appointment_booking_schedule"
        ordering = ["weekday"]
        constraints = [
            models.UniqueConstraint(
                fields=["config", "weekday"],
                name="appt_schedule_unique_weekday",
            ),
        ]
        indexes = [
            models.Index(
                fields=["config", "weekday", "is_active"],
                name="appt_schedule_lookup_idx",
            ),
        ]

    def __str__(self):
        return f"{self.config.chatbot} - {self.get_weekday_display()}"


class AppointmentBookingScheduleSlot(BaseModel):
    """One availability window within a weekday schedule."""

    schedule = models.ForeignKey(
        AppointmentBookingSchedule,
        on_delete=models.CASCADE,
        related_name="slots",
    )
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "appointment_booking_schedule_slot"
        ordering = ["start_time"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="appt_slot_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["schedule", "start_time", "end_time"],
                name="appt_slot_unique_window",
            ),
        ]
        indexes = [
            models.Index(
                fields=["schedule", "is_active", "start_time"],
                name="appt_slot_lookup_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError(
                {"end_time": "End time must be later than start time."}
            )
        if (
            self.is_active
            and self.schedule_id
            and self.start_time
            and self.end_time
        ):
            overlapping_slots = type(self).objects.filter(
                schedule_id=self.schedule_id,
                is_active=True,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            if self.pk:
                overlapping_slots = overlapping_slots.exclude(pk=self.pk)
            if overlapping_slots.exists():
                raise ValidationError(
                    "This time slot overlaps another active slot for the day."
                )

    def __str__(self):
        if not self.start_time or not self.end_time:
            return f"Slot for {self.schedule}"
        return (
            f"{self.schedule} "
            f"{self.start_time:%H:%M}-{self.end_time:%H:%M}"
        )


class AppointmentBookingClosedDate(BaseModel):
    """A date on which appointment booking is unavailable."""

    config = models.ForeignKey(
        AppointmentBookingConfig,
        on_delete=models.CASCADE,
        related_name="closed_dates",
    )
    date = models.DateField()
    label = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Optional label, for example Eid holiday.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "appointment_booking_closed_date"
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["config", "date"],
                name="appt_closed_date_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["config", "date", "is_active"],
                name="appt_closed_date_lookup_idx",
            ),
        ]

    def __str__(self):
        description = self.label or "Closed"
        return f"{self.config.chatbot} - {self.date}: {description}"


class Appointment(BaseModel):
    """A booking displayed in a chatbot's appointment list."""

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        on_delete=models.CASCADE,
        related_name="appointments",
    )
    collected_fields = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_collected_fields],
        help_text="Values collected using the chatbot's booking configuration.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_appointment_metadata],
    )

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=AppointmentStatus.choices,
        default=AppointmentStatus.PENDING,
        db_index=True,
    )
    notes = models.TextField(blank=True, default="")
    cancellation_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "appointment"
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")),
                name="appointment_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["chatbot", "starts_at"],
                condition=Q(
                    status__in=[
                        AppointmentStatus.PENDING,
                        AppointmentStatus.CONFIRMED,
                    ]
                ),
                name="appointment_unique_open_start",
            ),
        ]
        indexes = [
            models.Index(
                fields=["chatbot", "starts_at"],
                name="appointment_bot_start_idx",
            ),
            models.Index(
                fields=["chatbot", "status", "starts_at"],
                name="appointment_list_idx",
            ),
        ]

    def clean(self):
        super().clean()
        self._validate_collected_fields_against_config()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError(
                {"ends_at": "End time must be later than start time."}
            )
        if self.chatbot_id and self.starts_at and self.ends_at and self.status in {
            AppointmentStatus.PENDING,
            AppointmentStatus.CONFIRMED,
        }:
            overlapping_appointments = type(self).objects.filter(
                chatbot_id=self.chatbot_id,
                status__in=[
                    AppointmentStatus.PENDING,
                    AppointmentStatus.CONFIRMED,
                ],
                starts_at__lt=self.ends_at,
                ends_at__gt=self.starts_at,
            )
            if self.pk:
                overlapping_appointments = overlapping_appointments.exclude(
                    pk=self.pk
                )
            if overlapping_appointments.exists():
                raise ValidationError(
                    "This time overlaps another pending or confirmed appointment."
                )

    def _validate_collected_fields_against_config(self):
        if (
            not self.chatbot_id
            or not isinstance(self.collected_fields, dict)
            or not all(
                isinstance(key, str) for key in self.collected_fields
            )
        ):
            return

        try:
            config = AppointmentBookingConfig.objects.get(
                chatbot_id=self.chatbot_id
            )
        except AppointmentBookingConfig.DoesNotExist:
            raise ValidationError(
                {
                    "collected_fields": (
                        "The chatbot does not have an appointment booking "
                        "configuration."
                    )
                }
            )

        config_fields = (
            config.collectable_fields
            if isinstance(config.collectable_fields, list)
            else []
        )
        configured_fields = {
            field["value"]: field
            for field in config_fields
            if isinstance(field, dict)
            and isinstance(field.get("value"), str)
        }
        visible_fields = {
            value: field
            for value, field in configured_fields.items()
            if field.get("mode") != AppointmentFieldMode.HIDDEN
        }

        unknown_fields = set(self.collected_fields) - set(visible_fields)
        if unknown_fields:
            raise ValidationError(
                {
                    "collected_fields": (
                        "Fields are not collectable for this configuration: "
                        f"{', '.join(sorted(unknown_fields))}."
                    )
                }
            )

        missing_fields = [
            value
            for value, field in visible_fields.items()
            if field.get("mode") == AppointmentFieldMode.REQUIRED
            and self._is_empty_collected_value(
                self.collected_fields.get(value)
            )
        ]
        if missing_fields:
            raise ValidationError(
                {
                    "collected_fields": (
                        "Required fields are missing: "
                        f"{', '.join(sorted(missing_fields))}."
                    )
                }
            )

        for value, collected_value in self.collected_fields.items():
            if self._is_empty_collected_value(collected_value):
                continue
            field_type = visible_fields[value].get("type")
            if field_type == "text" and not isinstance(collected_value, str):
                raise ValidationError(
                    {"collected_fields": f"'{value}' must be text."}
                )
            if field_type == "email":
                if not isinstance(collected_value, str):
                    raise ValidationError(
                        {"collected_fields": f"'{value}' must be an email."}
                    )
                try:
                    validate_email(collected_value)
                except ValidationError:
                    raise ValidationError(
                        {"collected_fields": f"'{value}' must be a valid email."}
                    )
            if field_type == "date" and (
                not isinstance(collected_value, str)
                or parse_date(collected_value) is None
            ):
                raise ValidationError(
                    {
                        "collected_fields": (
                            f"'{value}' must be a date in YYYY-MM-DD format."
                        )
                    }
                )

    @staticmethod
    def _is_empty_collected_value(value):
        return value is None or (isinstance(value, str) and not value.strip())

    def save(self, *args, **kwargs):
        if isinstance(self.collected_fields, dict):
            self.collected_fields = dict(self.collected_fields)
            email = self.collected_fields.get("email")
            if isinstance(email, str):
                self.collected_fields["email"] = email.strip().casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        fields = (
            self.collected_fields
            if isinstance(self.collected_fields, dict)
            else {}
        )
        identity = (
            fields.get("name")
            or fields.get("email")
            or fields.get("phone")
            or str(self.id)
        )
        if not self.starts_at:
            return str(identity)
        return f"{identity} - {self.starts_at:%Y-%m-%d %H:%M}"
