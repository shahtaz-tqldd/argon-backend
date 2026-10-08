from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from appointment.models import (
    AppointmentBookingClosedDate,
    AppointmentBookingConfig,
    AppointmentBookingSchedule,
    AppointmentBookingScheduleSlot,
)
from appointment.utils.choices import Weekday
from chatbot.models import Chatbot, ChatbotConfig
from chatbot.services.chatbot_config import get_chatbot_capacity
from subscription.utils.choices import PlanFeature


DEFAULT_WEEKDAYS = "mon-fri"
DEFAULT_HOURS = "09:00-17:00"


class Command(BaseCommand):
    help = (
        "Enable appointment booking for a chatbot and sync its weekly "
        "schedule, time slots, and closed dates."
    )

    # python manage.py create_example_appointment_config --chatbot support-bot
    # python manage.py create_example_appointment_config --chatbot support-bot \
    #     --weekdays mon-sat --hours "09:00-12:00,13:00-17:00" \
    #     --duration-minutes 45 --advance-days 60 --max-per-day 20
    # python manage.py create_example_appointment_config --chatbot support-bot \
    #     --slot sat:10:00-14:00 --slot sun:11:00-13:00 \
    #     --closed-date 2026-10-05="Eid holiday" --force

    def add_arguments(self, parser):
        parser.add_argument(
            "--chatbot",
            required=True,
            help="Chatbot slug or UUID.",
        )
        parser.add_argument(
            "--user-email",
            help="User recorded as the actor. Defaults to the chatbot's creator.",
        )
        parser.add_argument(
            "--weekdays",
            help=(
                "Weekday scope for the schedule, for example mon-fri, "
                "mon,wed,fri or 0-4. Defaults to mon-fri when schedule "
                "options are used."
            ),
        )
        parser.add_argument(
            "--hours",
            help=(
                'Comma-separated time windows applied to every weekday in '
                'scope, for example "09:00-12:00,13:00-17:00". '
                'Defaults to 09:00-17:00.'
            ),
        )
        parser.add_argument(
            "--slot",
            action="append",
            default=[],
            metavar="WEEKDAY:HH:MM-HH:MM",
            help=(
                "Per-weekday window that overrides --hours for that day. "
                "Repeatable."
            ),
        )
        parser.add_argument(
            "--closed-date",
            action="append",
            default=[],
            metavar="YYYY-MM-DD[=LABEL]",
            help="Date the chatbot stays closed, with an optional label.",
        )
        parser.add_argument(
            "--duration-minutes",
            type=int,
            help="Appointment duration in minutes.",
        )
        parser.add_argument(
            "--advance-days",
            type=int,
            help="How many days ahead visitors can book.",
        )
        parser.add_argument(
            "--max-per-day",
            type=int,
            help="Daily appointment limit; 0 means no limit.",
        )
        parser.add_argument(
            "--confirmation-message",
            help="Message shown after a successful booking.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Activate even when the plan lacks the appointment feature.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        chatbot = self.get_chatbot(options["chatbot"])
        user = self.get_user(options["user_email"], chatbot)
        self.check_plan_feature(chatbot, force=options["force"])

        config = self.upsert_config(chatbot=chatbot, user=user, options=options)
        self.stdout.write(
            f"Appointment booking enabled for {chatbot.chatbot_name} "
            f"({chatbot.slug}): duration="
            f"{config.appointment_duration_minutes}m, "
            f"advance={config.maximum_advance_days}d, "
            f"daily_limit={config.max_appointments_per_day or 'none'}."
        )

        weekly_schedule = self.build_weekly_schedule(options)
        if weekly_schedule is not None:
            self.sync_schedules(
                config=config,
                user=user,
                weekly_schedule=weekly_schedule,
            )
            for weekday, windows in sorted(weekly_schedule.items()):
                self.stdout.write(
                    f"  {Weekday(weekday).label}: "
                    + ", ".join(
                        f"{start:%H:%M}-{end:%H:%M}"
                        for start, end in windows
                    )
                )

        if options["closed_date"]:
            closed_dates = self.sync_closed_dates(
                config=config,
                user=user,
                raw_closed_dates=options["closed_date"],
            )
            for closed_date in closed_dates:
                label = f" ({closed_date.label})" if closed_date.label else ""
                self.stdout.write(
                    f"  Closed {closed_date.date}{label}"
                )

        self.stdout.write(self.style.SUCCESS("Appointment booking activated."))

    def get_chatbot(self, chatbot_reference):
        chatbot = Chatbot.objects.filter(
            slug=chatbot_reference,
            is_deleted=False,
        ).first()
        if chatbot is None:
            chatbot = Chatbot.objects.filter(
                pk=chatbot_reference,
                is_deleted=False,
            ).first()
        if chatbot is None:
            raise CommandError(f"Chatbot not found: {chatbot_reference}")
        return chatbot

    def get_user(self, user_email, chatbot):
        user_model = get_user_model()
        if user_email:
            user = (
                user_model.objects.filter(
                    email__iexact=user_email.strip().casefold(),
                )
                .first()
            )
            if user is None:
                raise CommandError(f"User not found: {user_email}")
            return user
        return chatbot.created_by

    def check_plan_feature(self, chatbot, *, force):
        try:
            capacity = get_chatbot_capacity(chatbot)
        except ChatbotConfig.DoesNotExist:
            message = (
                "Chatbot capacity has not been initialized; the chatbot has "
                "no applied subscription."
            )
            if not force:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(f"{message} Continuing."))
            return

        if capacity.has_feature(PlanFeature.APPOINTMENT_BOOKING):
            return
        message = (
            "The active subscription does not include appointment booking."
        )
        if not force:
            raise CommandError(message)
        self.stdout.write(self.style.WARNING(f"{message} Continuing."))

    def upsert_config(self, *, chatbot, user, options):
        config, created = AppointmentBookingConfig.objects.get_or_create(
            chatbot=chatbot,
            defaults={
                "created_by": user,
                "updated_by": user,
            },
        )
        if created:
            self.stdout.write("Created appointment booking config.")

        if options["duration_minutes"] is not None:
            config.appointment_duration_minutes = options["duration_minutes"]
        if options["advance_days"] is not None:
            config.maximum_advance_days = options["advance_days"]
        if options["max_per_day"] is not None:
            config.max_appointments_per_day = (
                options["max_per_day"] or None
            )
        if options["confirmation_message"] is not None:
            config.confirmation_message = options["confirmation_message"]

        config.is_enabled = True
        config.updated_by = user
        try:
            config.full_clean()
            config.save()
        except ValidationError as exc:
            raise CommandError(f"Invalid booking config: {exc}") from exc
        return config

    def build_weekly_schedule(self, options):
        """Return {weekday: [(start, end)]} or None when nothing to sync."""

        weekday_windows = {}

        if options["weekdays"] or options["hours"]:
            weekdays = self.parse_weekdays(
                options["weekdays"] or DEFAULT_WEEKDAYS
            )
            windows = self.parse_windows(
                options["hours"] or DEFAULT_HOURS,
                label="--hours",
            )
            for weekday in weekdays:
                weekday_windows[weekday] = list(windows)

        for slot_reference in options["slot"]:
            weekday_token, _, window_text = slot_reference.partition(":")
            weekday = self.parse_weekday(weekday_token.strip())
            weekday_windows[weekday] = [
                self.parse_window(window_text.strip(), label="--slot")
            ]

        return weekday_windows or None

    def parse_weekdays(self, weekdays_text):
        weekdays = []
        for item in weekdays_text.split(","):
            item = item.strip().lower()
            if not item:
                continue
            if "-" in item:
                start_text, _, end_text = item.partition("-")
                start = self.parse_weekday(start_text)
                end = self.parse_weekday(end_text)
                if start <= end:
                    weekdays.extend(range(start, end + 1))
                else:
                    weekdays.extend(range(start, 7))
                    weekdays.extend(range(0, end + 1))
            else:
                weekdays.append(self.parse_weekday(item))
        if not weekdays:
            raise CommandError(f"No valid weekday in: {weekdays_text}")
        return sorted(set(weekdays))

    def parse_weekday(self, weekday_token):
        text = weekday_token.strip().lower()
        if text.isdigit():
            value = int(text)
            if 0 <= value <= 6:
                return value
            raise CommandError(f"Weekday out of range: {weekday_token}")

        if len(text) < 3:
            raise CommandError(
                f"Ambiguous weekday: {weekday_token}. "
                "Use at least three letters."
            )
        matches = [
            weekday.value
            for weekday in Weekday
            if weekday.label.lower().startswith(text)
        ]
        if len(matches) != 1:
            raise CommandError(f"Unknown weekday: {weekday_token}")
        return matches[0]

    def parse_windows(self, windows_text, *, label):
        windows = [
            self.parse_window(window.strip(), label=label)
            for window in windows_text.split(",")
            if window.strip()
        ]
        if not windows:
            raise CommandError(f"No time window found in {label}.")
        return windows

    def parse_window(self, window_text, *, label):
        start_text, separator, end_text = window_text.partition("-")
        if not separator:
            raise CommandError(
                f"{label} must look like HH:MM-HH:MM, got: {window_text}"
            )
        start = self.parse_time(start_text.strip(), label=label)
        end = self.parse_time(end_text.strip(), label=label)
        if end <= start:
            raise CommandError(
                f"{label} end time must be later than start time: "
                f"{window_text}"
            )
        return start, end

    @staticmethod
    def parse_time(time_text, *, label):
        parts = time_text.split(":")
        if (
            len(parts) != 2
            or not parts[0].isdigit()
            or not parts[1].isdigit()
        ):
            raise CommandError(
                f"{label} time must look like HH:MM, got: {time_text}"
            )
        hour, minute = int(parts[0]), int(parts[1])
        try:
            return time(hour, minute)
        except ValueError as exc:
            raise CommandError(
                f"{label} time is invalid: {time_text}"
            ) from exc

    def sync_schedules(self, *, config, user, weekly_schedule):
        config.schedules.exclude(
            weekday__in=weekly_schedule,
        ).delete()

        existing_schedules = {
            schedule.weekday: schedule
            for schedule in config.schedules.select_for_update()
        }
        for weekday, windows in weekly_schedule.items():
            schedule = existing_schedules.get(weekday)
            if schedule is None:
                schedule = AppointmentBookingSchedule(
                    config=config,
                    weekday=weekday,
                    created_by=user,
                )
            schedule.is_active = True
            schedule.updated_by = user
            schedule.save()

            schedule.slots.all().delete()
            for start_time, end_time in windows:
                slot = AppointmentBookingScheduleSlot(
                    schedule=schedule,
                    start_time=start_time,
                    end_time=end_time,
                    is_active=True,
                    created_by=user,
                    updated_by=user,
                )
                try:
                    slot.full_clean()
                    slot.save()
                except ValidationError as exc:
                    raise CommandError(
                        f"Invalid slot for {Weekday(weekday).label} "
                        f"{start_time:%H:%M}-{end_time:%H:%M}: {exc}"
                    ) from exc

    def sync_closed_dates(self, *, config, user, raw_closed_dates):
        closed_dates = []
        for raw_closed_date in raw_closed_dates:
            date_text, _, label = raw_closed_date.partition("=")
            try:
                closed_on = date.fromisoformat(date_text.strip())
            except ValueError as exc:
                raise CommandError(
                    f"Closed date must be YYYY-MM-DD, got: {date_text}"
                ) from exc

            closed_date, _ = AppointmentBookingClosedDate.objects.update_or_create(
                config=config,
                date=closed_on,
                defaults={
                    "label": label.strip(),
                    "is_active": True,
                    "updated_by": user,
                },
            )
            closed_dates.append(closed_date)
        return closed_dates
