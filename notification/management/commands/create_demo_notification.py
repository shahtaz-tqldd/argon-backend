import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from chatbot.models import Chatbot
from notification.models import NotificationRecipientType, NotificationType
from notification.services import create_notification
from workspace.models import Workspace


class Command(BaseCommand):
    help = "Create and emit a demo notification."

    def add_arguments(self, parser):
        parser.add_argument(
            "--recipient-type",
            choices=NotificationRecipientType.values,
            default=NotificationRecipientType.GLOBAL,
            help="Audience type. Defaults to global.",
        )
        parser.add_argument(
            "--notification-type",
            choices=NotificationType.values,
            default=NotificationType.NOTIFY,
            help="Notification event type. Defaults to notify.",
        )
        parser.add_argument(
            "--recipient-id",
            help="User UUID. Required when --recipient-type=user.",
        )
        parser.add_argument(
            "--target-id",
            help=(
                "Workspace, chatbot, or chat-session UUID. Required for those "
                "recipient types."
            ),
        )
        parser.add_argument("--title", default="Demo notification")
        parser.add_argument("--message", default="This is a demo notification.")
        parser.add_argument(
            "--metadata",
            default="{}",
            help='JSON object metadata. Example: --metadata \'{"source":"demo"}\'',
        )

    def handle(self, *args, **options):
        metadata = self.parse_metadata(options["metadata"])
        recipient_type = options["recipient_type"]
        recipient = self.get_recipient(recipient_type, options.get("recipient_id"))
        target_id = options.get("target_id")
        scope_targets = self.get_scope_targets(recipient_type, target_id)

        notification = create_notification(
            recipient_type=recipient_type,
            notification_type=options["notification_type"],
            recipient=recipient,
            title=options["title"],
            message=options["message"],
            metadata=metadata,
            **scope_targets,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {recipient_type} demo notification {notification.id}."
            )
        )

    def get_recipient(self, recipient_type, recipient_id):
        if recipient_type != NotificationRecipientType.USER:
            if recipient_id:
                raise CommandError(
                    "--recipient-id can only be used with --recipient-type=user."
                )
            return None

        if not recipient_id:
            raise CommandError("--recipient-id is required when --recipient-type=user.")

        user = get_user_model().objects.filter(pk=recipient_id).first()
        if user is None:
            raise CommandError(f"User not found: {recipient_id}")
        return user

    def get_scope_targets(self, recipient_type, target_id):
        if recipient_type == NotificationRecipientType.WORKSPACE:
            if not target_id:
                raise CommandError("--target-id is required for workspace.")
            workspace = Workspace.objects.filter(pk=target_id).first()
            if workspace is None:
                raise CommandError(f"Workspace not found: {target_id}")
            return {"workspace": workspace}

        if recipient_type == NotificationRecipientType.CHATBOT:
            if not target_id:
                raise CommandError("--target-id is required for chatbot.")
            chatbot = Chatbot.objects.filter(pk=target_id).first()
            if chatbot is None:
                raise CommandError(f"Chatbot not found: {target_id}")
            return {"chatbot": chatbot}

        if recipient_type == NotificationRecipientType.CHAT_SESSION:
            if not target_id:
                raise CommandError("--target-id is required for chat_session.")
            return {"target_id": target_id}

        if target_id:
            raise CommandError(
                "--target-id can only be used with workspace, chatbot, or "
                "chat_session."
            )
        return {}

    def parse_metadata(self, raw_metadata):
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError as exc:
            raise CommandError(f"--metadata must be valid JSON: {exc}") from exc

        if not isinstance(metadata, dict):
            raise CommandError("--metadata must be a JSON object.")
        return metadata
