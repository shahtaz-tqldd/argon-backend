from django.contrib import admin
from django.utils.html import format_html

from appointment_booking.models import (
    Appointment,
    AppointmentBookingClosedDate,
    AppointmentBookingConfig,
    AppointmentBookingSchedule,
    AppointmentBookingScheduleSlot,
)


class AppointmentBookingScheduleSlotInline(admin.TabularInline):
    model = AppointmentBookingScheduleSlot
    extra = 1
    fields = ("start_time", "end_time", "is_active")


class AppointmentBookingScheduleInline(admin.StackedInline):
    model = AppointmentBookingSchedule
    extra = 0
    show_change_link = True
    fields = ("weekday", "is_active")


class AppointmentBookingClosedDateInline(admin.TabularInline):
    model = AppointmentBookingClosedDate
    extra = 1
    fields = ("date", "label", "is_active")


@admin.register(AppointmentBookingConfig)
class AppointmentBookingConfigAdmin(admin.ModelAdmin):
    list_display = (
        "chatbot",
        "is_enabled",
        "appointment_duration_minutes",
        "maximum_advance_days",
        "max_appointments_per_day",
        "updated_at",
    )
    list_filter = ("is_enabled",)
    search_fields = ("chatbot__chatbot_name", "chatbot__name", "chatbot__id")
    readonly_fields = ("created_at", "updated_at")
    inlines = [
        AppointmentBookingScheduleInline,
        AppointmentBookingClosedDateInline,
    ]
    fieldsets = (
        (
            None,
            {
                "fields": ("chatbot", "is_enabled"),
            },
        ),
        (
            "Booking Rules",
            {
                "fields": (
                    "appointment_duration_minutes",
                    "maximum_advance_days",
                    "max_appointments_per_day",
                    "confirmation_message",
                ),
            },
        ),
        (
            "Field Schema",
            {
                "classes": ("collapse",),
                "fields": ("collectable_fields",),
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )


@admin.register(AppointmentBookingSchedule)
class AppointmentBookingScheduleAdmin(admin.ModelAdmin):
    list_display = ("config", "get_weekday_display", "is_active", "updated_at")
    list_filter = ("weekday", "is_active")
    search_fields = (
        "config__chatbot__chatbot_name",
        "config__chatbot__name",
    )
    readonly_fields = ("created_at", "updated_at")
    inlines = [AppointmentBookingScheduleSlotInline]

    @admin.display(description="Weekday", ordering="weekday")
    def get_weekday_display(self, obj):
        return obj.get_weekday_display()


@admin.register(AppointmentBookingScheduleSlot)
class AppointmentBookingScheduleSlotAdmin(admin.ModelAdmin):
    list_display = (
        "schedule",
        "start_time",
        "end_time",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "schedule__weekday")
    search_fields = (
        "schedule__config__chatbot__chatbot_name",
        "schedule__config__chatbot__name",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(AppointmentBookingClosedDate)
class AppointmentBookingClosedDateAdmin(admin.ModelAdmin):
    list_display = ("config", "date", "label", "is_active", "updated_at")
    list_filter = ("is_active", "date")
    search_fields = (
        "label",
        "config__chatbot__chatbot_name",
        "config__chatbot__name",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "client_display",
        "chatbot",
        "starts_at",
        "ends_at",
        "status",
        "created_at",
    )
    list_filter = ("status", "starts_at", "chatbot")
    search_fields = (
        "collected_fields__name",
        "collected_fields__email",
        "collected_fields__phone",
        "chatbot__chatbot_name",
        "chatbot__name",
    )
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "starts_at"
    fieldsets = (
        (
            "Overview",
            {
                "fields": ("chatbot", "status", ("starts_at", "ends_at")),
            },
        ),
        (
            "Customer Details",
            {
                "fields": ("collected_fields", "metadata"),
            },
        ),
        (
            "Notes & Status Changes",
            {
                "fields": ("notes", "cancellation_reason"),
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    @admin.display(description="Client")
    def client_display(self, obj):
        data = obj.collected_fields if isinstance(obj.collected_fields, dict) else {}
        name = data.get("name")
        email = data.get("email")
        phone = data.get("phone")

        primary = name or email or phone or f"Appt #{obj.pk}"
        secondary = email if name and email else (phone if name else None)

        if secondary:
            return format_html(
                "<strong>{}</strong><br><small style='color: gray;'>{}</small>",
                primary,
                secondary,
            )
        return primary
