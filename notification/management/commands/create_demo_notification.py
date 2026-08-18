import json

from django.core.management.base import BaseCommand, CommandError

from notification.services import create_global_notification, create_trip_notification
from trips.models import Trip


class Command(BaseCommand):
    help = "Create a demo global or trip notification and emit it over the notification socket."

    # python manage.py create_demo_notification --type global
    # python manage.py create_demo_notification \
    # --type trip \
    # --trip-id <trip_id>


    def add_arguments(self, parser):
        parser.add_argument(
            "--type",
            choices=["global", "trip"],
            default="global",
            help="Notification type to create. Defaults to global.",
        )
        parser.add_argument(
            "--trip-id",
            help="Trip id for trip notifications.",
        )
        parser.add_argument(
            "--title",
            default="Demo notification",
            help="Notification title.",
        )
        parser.add_argument(
            "--message",
            default="This is a demo notification.",
            help="Notification message.",
        )
        parser.add_argument(
            "--metadata",
            default="{}",
            help='JSON object metadata. Example: --metadata \'{"source":"demo"}\'',
        )

    def handle(self, *args, **options):
        metadata = self.parse_metadata(options["metadata"])
        notification_type = options["type"]

        if notification_type == "global":
            notification = create_global_notification(
                title=options["title"],
                message=options["message"],
                metadata=metadata,
            )
            self.stdout.write(
                self.style.SUCCESS(f"Created global demo notification {notification.id}.")
            )
            return

        trip_id = options.get("trip_id")
        if not trip_id:
            raise CommandError("--trip-id is required when --type=trip.")

        trip = Trip.objects.select_related("user").filter(pk=trip_id).first()
        if trip is None:
            raise CommandError(f"Trip not found: {trip_id}")

        notification = create_trip_notification(
            recipient=trip.user,
            trip=trip,
            title=options["title"],
            message=options["message"],
            metadata=metadata,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created trip demo notification {notification.id} for trip {trip.id}."
            )
        )

    def parse_metadata(self, raw_metadata):
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError as exc:
            raise CommandError(f"--metadata must be valid JSON: {exc}") from exc

        if not isinstance(metadata, dict):
            raise CommandError("--metadata must be a JSON object.")

        return metadata
