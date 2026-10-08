from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from chatbot.services.chatbot_config import apply_active_subscription_to_chatbot_capacity
from chatbot.services.membership import create_chatbot
from subscription.utils.choices import (
    BillingInterval,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription, PlanPrice, SubscriptionPlan
from subscription.services.subscriptions import (
    OPEN_SUBSCRIPTION_STATUSES,
    activate_free_subscription,
)
from workspace.models import Workspace


class Command(BaseCommand):
    help = (
        "Create a chatbot end to end: provision (or reuse) the owner's "
        "workspace, sync subscription plans, create the chatbot, and select "
        "the requested subscription plan."
    )

    # python manage.py create_example_chatbot --chatbot-name "Support Bot"
    # python manage.py create_example_chatbot --owner-email owner@example.com \
    #     --chatbot-name "Support Bot" --business-name "Acme" --plan growth

    def add_arguments(self, parser):
        parser.add_argument(
            "--owner-email",
            help=(
                "Email of the workspace owner. Defaults to the first "
                "superuser when omitted."
            ),
        )
        parser.add_argument(
            "--workspace-name",
            help="Name used when the workspace has to be created.",
        )
        parser.add_argument("--chatbot-name", required=True)
        parser.add_argument("--business-name", default="")
        parser.add_argument(
            "--plan",
            default="free",
            help="Subscription plan slug or name. Defaults to free.",
        )
        parser.add_argument(
            "--skip-plan-sync",
            action="store_true",
            help="Skip running create_subscription_plan first.",
        )

    def handle(self, *args, **options):
        owner = self.get_owner(options["owner_email"])
        workspace, workspace_created = self.get_workspace(
            owner=owner,
            name=options["workspace_name"],
        )
        self.stdout.write(
            f"Workspace {'created' if workspace_created else 'reused'}: "
            f"{workspace.name} ({workspace.slug})"
        )

        if not options["skip_plan_sync"]:
            call_command("create_subscription_plan", verbosity=0)
            self.stdout.write("Subscription plans synchronized.")

        plan, plan_price = self.get_plan(options["plan"])

        try:
            chatbot = create_chatbot(
                workspace=workspace,
                chatbot_name=options["chatbot_name"],
                business_name=options["business_name"],
                created_by=owner,
            )
        except ValidationError as exc:
            raise CommandError(f"Chatbot creation failed: {exc}") from exc

        subscription, subscription_created = self.select_subscription(
            chatbot=chatbot,
            plan=plan,
            plan_price=plan_price,
            user=owner,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Chatbot created: {chatbot.chatbot_name} ({chatbot.slug}, "
                f"id={chatbot.id})\n"
                f"Plan '{plan.slug}' "
                f"{'activated' if subscription_created else 'already active'} "
                f"(subscription id={subscription.id}, "
                f"status={subscription.status})."
            )
        )

    def get_owner(self, owner_email):
        user_model = get_user_model()
        if owner_email:
            owner = (
                user_model.objects.filter(
                    email__iexact=owner_email.strip().casefold(),
                )
                .first()
            )
            if owner is None:
                raise CommandError(f"Owner not found: {owner_email}")
            return owner

        owner = (
            user_model.objects.filter(is_superuser=True, is_active=True)
            .order_by("created_at")
            .first()
        )
        if owner is None:
            raise CommandError(
                "No --owner-email given and no active superuser exists."
            )
        return owner

    def get_workspace(self, *, owner, name):
        existing = (
            Workspace.objects.filter(owner=owner, name=name).order_by("created_at").first()
        )
        if existing is not None:
            return existing, False

        if not name:
            raise CommandError(
                "Workspace name is required when creating a new workspace."
            )
        
        workspace = Workspace.objects.create(
            owner=owner,
            name=name,
            created_by=owner,
        )

        return workspace, True


    def get_plan(self, plan_reference):
        plan = SubscriptionPlan.objects.filter(
            slug=plan_reference,
            is_active=True,
        ).first()
        if plan is None:
            plan = SubscriptionPlan.objects.filter(
                name__iexact=plan_reference,
                is_active=True,
            ).first()
        if plan is None:
            raise CommandError(
                f"Subscription plan not found: {plan_reference}. "
                "Run create_subscription_plan or pass --skip-plan-sync "
                "to use existing plans only."
            )

        plan_price = (
            PlanPrice.objects.filter(
                plan=plan,
                billing_interval=BillingInterval.MONTHLY,
                is_active=True,
            )
            .order_by("amount")
            .first()
        )
        if plan_price is None:
            raise CommandError(
                f"Plan '{plan.slug}' has no active monthly price; it cannot "
                "be selected by this command."
            )
        return plan, plan_price

    def select_subscription(self, *, chatbot, plan, plan_price, user):
        open_subscription = (
            ChatbotSubscription.objects.select_related("plan_price__plan")
            .filter(
                chatbot=chatbot,
                status__in=OPEN_SUBSCRIPTION_STATUSES,
            )
            .first()
        )
        if (
            open_subscription is not None
            and open_subscription.plan_price.plan_id == plan.pk
        ):
            return open_subscription, False

        if plan.is_free:
            subscription, _ = activate_free_subscription(
                chatbot=chatbot,
                plan_price=plan_price,
                user=user,
            )
            return subscription, True
        return (
            self.grant_subscription(
                chatbot=chatbot,
                plan_price=plan_price,
                user=user,
            ),
            True,
        )

    @transaction.atomic
    def grant_subscription(self, *, chatbot, plan_price, user):
        """Activate a paid plan directly, as an offline/manual contract."""

        now = timezone.now()
        open_subscriptions = (
            ChatbotSubscription.objects.select_for_update()
            .filter(
                chatbot=chatbot,
                status__in=OPEN_SUBSCRIPTION_STATUSES,
            )
        )
        for open_subscription in open_subscriptions:
            open_subscription.status = SubscriptionStatus.CANCELED
            open_subscription.canceled_at = now
            open_subscription.ended_at = now
            open_subscription.cancel_at_period_end = False
            open_subscription.updated_by = user
            open_subscription.save(
                update_fields=[
                    "status",
                    "canceled_at",
                    "ended_at",
                    "cancel_at_period_end",
                    "updated_by",
                    "updated_at",
                ]
            )

        subscription = ChatbotSubscription.objects.create(
            chatbot=chatbot,
            plan_price=plan_price,
            selected_by=user,
            provider=plan_price.provider,
            renewal_mode=RenewalMode.MANUAL,
            status=SubscriptionStatus.ACTIVE,
            started_at=now,
            created_by=user,
            updated_by=user,
        )
        apply_active_subscription_to_chatbot_capacity(subscription)
        return subscription
