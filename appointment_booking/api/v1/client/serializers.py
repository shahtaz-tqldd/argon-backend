from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from appointment_booking.models import (
    Appointment,
    AppointmentBookingClosedDate,
    AppointmentBookingConfig,
    AppointmentBookingSchedule,
    AppointmentBookingScheduleSlot,
)


def _raise_serializer_validation_error(exc):
    raise serializers.ValidationError(
        getattr(exc, "message_dict", exc.messages)
    ) from exc


class AppointmentChatbotQuerySerializer(serializers.Serializer):
    chatbot_slug = serializers.SlugField()


class AppointmentQuerySerializer(AppointmentChatbotQuerySerializer):
    appointment_id = serializers.UUIDField()


class AppointmentBookingConfigSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = AppointmentBookingConfig
        fields = (
            "id",
            "chatbot_id",
            "is_enabled",
            "collectable_fields",
            "appointment_duration_minutes",
            "maximum_advance_days",
            "max_appointments_per_day",
            "confirmation_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "chatbot_id", "created_at", "updated_at")

    def update(self, instance, validated_data):
        for field_name, field_value in validated_data.items():
            setattr(instance, field_name, field_value)
        instance.updated_by = self.context["request"].user
        try:
            instance.full_clean()
            instance.save()
        except DjangoValidationError as exc:
            _raise_serializer_validation_error(exc)
        return instance


class AppointmentBookingScheduleSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppointmentBookingScheduleSlot
        fields = ("id", "start_time", "end_time", "is_active")
        read_only_fields = ("id",)


class AppointmentBookingScheduleSerializer(serializers.ModelSerializer):
    slots = AppointmentBookingScheduleSlotSerializer(many=True)

    class Meta:
        model = AppointmentBookingSchedule
        fields = ("id", "weekday", "is_active", "slots")
        read_only_fields = ("id",)

    def validate_slots(self, slots):
        windows = set()
        active_slots = []
        for slot in slots:
            start_time = slot["start_time"]
            end_time = slot["end_time"]
            if end_time <= start_time:
                raise serializers.ValidationError(
                    "Each slot's end time must be later than its start time."
                )
            window = (start_time, end_time)
            if window in windows:
                raise serializers.ValidationError(
                    "A schedule cannot contain duplicate time slots."
                )
            windows.add(window)
            if slot.get("is_active", True):
                active_slots.append(window)

        active_slots.sort()
        for previous, current in zip(active_slots, active_slots[1:]):
            if current[0] < previous[1]:
                raise serializers.ValidationError(
                    "Active time slots for a day cannot overlap."
                )
        return slots


class AppointmentBookingClosedDateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppointmentBookingClosedDate
        fields = ("id", "date", "label", "is_active")
        read_only_fields = ("id",)


class AppointmentBookingAvailabilitySerializer(serializers.Serializer):
    schedules = AppointmentBookingScheduleSerializer(many=True, required=False)
    closed_dates = AppointmentBookingClosedDateSerializer(
        many=True,
        required=False,
    )

    def validate_schedules(self, schedules):
        weekdays = [schedule["weekday"] for schedule in schedules]
        if len(weekdays) != len(set(weekdays)):
            raise serializers.ValidationError(
                "Each weekday can only have one schedule."
            )
        return schedules

    def validate_closed_dates(self, closed_dates):
        dates = [closed_date["date"] for closed_date in closed_dates]
        if len(dates) != len(set(dates)):
            raise serializers.ValidationError(
                "Each closed date can only be supplied once."
            )
        return closed_dates

    @transaction.atomic
    def update(self, instance, validated_data):
        request_user = self.context["request"].user
        if "schedules" in validated_data:
            self._replace_schedules(
                instance,
                validated_data["schedules"],
                request_user,
            )
        if "closed_dates" in validated_data:
            self._replace_closed_dates(
                instance,
                validated_data["closed_dates"],
                request_user,
            )
        return instance

    def _replace_schedules(self, config, schedules_data, request_user):
        weekdays = [schedule["weekday"] for schedule in schedules_data]
        config.schedules.exclude(weekday__in=weekdays).delete()
        existing_schedules = {
            schedule.weekday: schedule
            for schedule in config.schedules.select_for_update()
        }

        for schedule_data in schedules_data:
            slots_data = schedule_data.pop("slots")
            weekday = schedule_data["weekday"]
            schedule = existing_schedules.get(weekday)
            if schedule is None:
                schedule = AppointmentBookingSchedule(
                    config=config,
                    created_by=request_user,
                )
            for field_name, field_value in schedule_data.items():
                setattr(schedule, field_name, field_value)
            schedule.updated_by = request_user
            try:
                schedule.full_clean()
                schedule.save()
            except DjangoValidationError as exc:
                _raise_serializer_validation_error(exc)

            schedule.slots.all().delete()
            for slot_data in slots_data:
                slot = AppointmentBookingScheduleSlot(
                    schedule=schedule,
                    created_by=request_user,
                    updated_by=request_user,
                    **slot_data,
                )
                try:
                    slot.full_clean()
                    slot.save()
                except DjangoValidationError as exc:
                    _raise_serializer_validation_error(exc)

    def _replace_closed_dates(self, config, closed_dates_data, request_user):
        dates = [closed_date["date"] for closed_date in closed_dates_data]
        config.closed_dates.exclude(date__in=dates).delete()
        existing_closed_dates = {
            closed_date.date: closed_date
            for closed_date in config.closed_dates.select_for_update()
        }

        for closed_date_data in closed_dates_data:
            date = closed_date_data["date"]
            closed_date = existing_closed_dates.get(date)
            if closed_date is None:
                closed_date = AppointmentBookingClosedDate(
                    config=config,
                    created_by=request_user,
                )
            for field_name, field_value in closed_date_data.items():
                setattr(closed_date, field_name, field_value)
            closed_date.updated_by = request_user
            try:
                closed_date.full_clean()
                closed_date.save()
            except DjangoValidationError as exc:
                _raise_serializer_validation_error(exc)


class AppointmentSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Appointment
        fields = (
            "id",
            "chatbot_id",
            "collected_fields",
            "metadata",
            "starts_at",
            "ends_at",
            "status",
            "notes",
            "cancellation_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "chatbot_id", "created_at", "updated_at")


class AppointmentUpdateSerializer(AppointmentSerializer):
    class Meta(AppointmentSerializer.Meta):
        read_only_fields = ("id", "chatbot_id", "created_at", "updated_at")

    def update(self, instance, validated_data):
        for field_name, field_value in validated_data.items():
            setattr(instance, field_name, field_value)
        instance.updated_by = self.context["request"].user
        try:
            instance.full_clean()
            instance.save()
        except DjangoValidationError as exc:
            _raise_serializer_validation_error(exc)
        return instance
