import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from chatbot.models import Chatbot
from lead_capture.models import LeadAIInsight
from lead_capture.services.insights import (
    MESSAGE_EXCERPT_LENGTH,
    VISITOR_MESSAGE_LIMIT,
)


TOPIC_POOL = (
    ("Pricing and plans", "Mostly Growth vs. Enterprise comparisons."),
    ("Slack integration", "Setup steps and notification routing."),
    ("API rate limits", "Monthly call caps on paid plans."),
    ("Onboarding", "Importing existing knowledge bases."),
    ("Mobile SDK", "iOS and Android widget support."),
    ("Invoicing", "Annual billing and payment methods."),
)

QUESTION_POOL = (
    ("Does the Growth plan include Slack support?", 3),
    ("How many API calls can we make per month?", 5),
    ("Can we export our conversation data?", 2),
    ("Do you offer annual discounts?", 4),
    ("Is there a mobile SDK for the widget?", 2),
    ("How long does onboarding usually take?", 3),
)

INTENT_POOL = (
    ("pricing", 5),
    ("demo request", 3),
    ("integrations", 4),
    ("support", 2),
    ("booking", 1),
)

IMPROVEMENT_POOL = (
    "Document the Slack setup steps in the knowledge base.",
    "Add a pricing comparison table to the welcome message.",
    "Publish rate-limit details per plan in the docs.",
    "Prepare an onboarding checklist reply for new workspaces.",
    "Clarify annual billing options earlier in the conversation.",
)

SUMMARY_TEMPLATES = (
    "Visitors focused on {primary} and {secondary}; {sessions} sessions "
    "showed clear buying intent and several asked follow-up pricing "
    "questions.",
    "The week was dominated by {primary} questions, with {secondary} a "
    "close second. {sessions} sessions escalated to a human agent after "
    "AI answers covered the basics.",
    "Activity was steady: {sessions} sessions, mostly around {primary}. "
    "Repeated questions about {secondary} suggest a knowledge-base gap.",
    "Fewer visitors than usual, but {primary} interest was strong and "
    "{secondary} questions came from qualified teams comparing vendors.",
)


class Command(BaseCommand):
    help = (
        "Create deterministic demo weekly AI lead insights for an existing "
        "chatbot."
    )

    # python manage.py create_example_lead_insights --bot-id <chatbot-id-or-slug>

    def add_arguments(self, parser):
        parser.add_argument(
            "--bot-id",
            required=True,
            help="ID or slug of the chatbot to create demo insights against.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=4,
            help="Number of past weeks to fill. Defaults to 4.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=1,
            help=(
                "Seed used for repeatable data. Reusing it updates the same "
                "demo insights. Defaults to 1."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        count = options["count"]
        if count < 1:
            raise CommandError("--count must be at least 1.")
        if count > 52:
            raise CommandError("--count cannot be greater than 52.")

        chatbot = self.get_chatbot(options["bot_id"])
        today = timezone.localdate()
        this_week_monday = today - timedelta(days=today.weekday())

        randomizer = random.Random(options["seed"])
        created_count = 0
        updated_count = 0
        for weeks_ago in range(1, count + 1):
            week_start = this_week_monday - timedelta(days=7 * weeks_ago)
            week_end = week_start + timedelta(days=6)
            values = self._insight_values(
                weeks_ago=weeks_ago,
                randomizer=randomizer,
            )
            insight, created = LeadAIInsight.objects.update_or_create(
                chatbot=chatbot,
                week_start=week_start,
                defaults={**values, "week_end": week_end},
            )
            created_count += int(created)
            updated_count += int(not created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo lead AI insights ready for '{chatbot.slug}': "
                f"{created_count} created, {updated_count} updated across "
                f"{count} week(s)."
            )
        )

    def get_chatbot(self, bot_reference):
        chatbot = None
        try:
            chatbot = (
                Chatbot.objects.filter(is_deleted=False)
                .select_related("workspace")
                .get(pk=bot_reference)
            )
        except (ValueError, Chatbot.DoesNotExist):
            chatbot = (
                Chatbot.objects.filter(is_deleted=False, slug=bot_reference)
                .select_related("workspace")
                .first()
            )
        if chatbot is None:
            raise CommandError(f"Active chatbot not found: {bot_reference}")
        if not chatbot.workspace.is_active:
            raise CommandError(
                f"The workspace for chatbot '{chatbot.slug}' is inactive."
            )
        return chatbot

    @staticmethod
    def _insight_values(*, weeks_ago, randomizer):
        # Older weeks get slightly less traffic so the list reads naturally.
        visitor_message_count = max(
            5,
            80 - (weeks_ago - 1) * 9 + randomizer.randint(-6, 12),
        )
        session_count = max(2, visitor_message_count // 4)

        topics = randomizer.sample(TOPIC_POOL, 3)
        questions = randomizer.sample(QUESTION_POOL, 3)
        intents = randomizer.sample(INTENT_POOL, 3)
        improvements = randomizer.sample(IMPROVEMENT_POOL, 2)

        summary = randomizer.choice(SUMMARY_TEMPLATES).format(
            primary=topics[0][0].lower(),
            secondary=topics[1][0].lower(),
            sessions=session_count,
        )
        return {
            "session_count": session_count,
            "visitor_message_count": visitor_message_count,
            "summary": summary,
            "topics": [
                {
                    "topic": name,
                    "mentions": max(
                        1,
                        min(
                            visitor_message_count // (index + 2),
                            visitor_message_count,
                        ),
                    ),
                    "note": note,
                }
                for index, (name, note) in enumerate(topics)
            ],
            "frequently_asked_questions": [
                {
                    "question": question,
                    "times_asked": min(
                        max(1, base + randomizer.randint(-1, 2)),
                        visitor_message_count,
                    ),
                }
                for question, base in questions
            ],
            "common_intents": [
                {
                    "intent": intent,
                    "mentions": min(
                        max(1, base + randomizer.randint(0, 2)),
                        visitor_message_count,
                    ),
                }
                for intent, base in intents
            ],
            "areas_of_improvement": list(improvements),
            "metadata": {
                "model": "demo",
                "analyzed_message_count": visitor_message_count,
                "message_limit": VISITOR_MESSAGE_LIMIT,
                "excerpt_length": MESSAGE_EXCERPT_LENGTH,
            },
        }
