import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from chatbot.models import Chatbot
from lead_capture.models import Lead, LeadCaptureConfig
from lead_capture.utils.choices import LeadStatusType


FIRST_NAMES = (
    "Aisha",
    "Amelia",
    "Arjun",
    "Daniel",
    "Elena",
    "Fatima",
    "Grace",
    "Hasan",
    "James",
    "Liam",
    "Maya",
    "Noah",
    "Olivia",
    "Priya",
    "Rafi",
    "Sofia",
    "Tariq",
    "William",
    "Yuki",
    "Zara",
)

LAST_NAMES = (
    "Ahmed",
    "Brown",
    "Chen",
    "Davis",
    "Garcia",
    "Hassan",
    "Johnson",
    "Khan",
    "Kim",
    "Martin",
    "Miller",
    "Patel",
    "Rahman",
    "Silva",
    "Singh",
    "Smith",
    "Taylor",
    "Thomas",
    "Wilson",
    "Wong",
)

COMPANIES = (
    "Acme Labs",
    "Bluebird Analytics",
    "BrightPath Studio",
    "Cloudline Systems",
    "Evergreen Commerce",
    "Harbor & Co.",
    "Northstar Digital",
    "Orbit Works",
    "Pioneer Health",
    "Summit Learning",
)

INTERESTS = (
    "analytics",
    "customer_support",
    "enterprise_plan",
    "integrations",
    "product_demo",
)

TEAM_SIZES = ("1-10", "11-50", "51-200", "201-500", "500+")

LOCATIONS = (
    ("BD", "Dhaka", "12 Gulshan Avenue", "+880"),
    ("CA", "Toronto", "85 King Street", "+1"),
    ("DE", "Berlin", "24 Friedrichstrasse", "+49"),
    ("GB", "London", "14 King Street", "+44"),
    ("IN", "Bengaluru", "42 Residency Road", "+91"),
    ("JP", "Tokyo", "8 Shibuya Crossing", "+81"),
    ("SG", "Singapore", "30 Raffles Place", "+65"),
    ("US", "Austin", "210 Market Street", "+1"),
)

SCORE_RANGES = {
    LeadStatusType.NEW: (20, 60),
    LeadStatusType.QUALIFIED: (65, 95),
    LeadStatusType.CONTACTED: (45, 85),
    LeadStatusType.CONVERTED: (80, 100),
    LeadStatusType.DISQUALIFIED: (0, 35),
}


class Command(BaseCommand):
    help = "Create deterministic demo leads for an existing chatbot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--chatbot-slug",
            required=True,
            help="Slug of the chatbot that will own the demo leads.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=20,
            help="Number of demo leads to create or update. Defaults to 20.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=1,
            help=(
                "Seed used for repeatable data. Reusing it updates the same "
                "demo leads. Defaults to 1."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        count = options["count"]
        if count < 1:
            raise CommandError("--count must be at least 1.")
        if count > 1000:
            raise CommandError("--count cannot be greater than 1000.")

        chatbot_slug = options["chatbot_slug"]
        chatbot = (
            Chatbot.objects.filter(slug=chatbot_slug, is_deleted=False)
            .select_related("workspace")
            .first()
        )
        if chatbot is None:
            raise CommandError(f"Active chatbot not found: {chatbot_slug}")
        if not chatbot.workspace.is_active:
            raise CommandError(
                f"The workspace for chatbot '{chatbot_slug}' is inactive."
            )

        created_count = 0
        updated_count = 0
        randomizer = random.Random(options["seed"])
        self._ensure_demo_config(chatbot)

        for index in range(1, count + 1):
            values = self._lead_values(
                chatbot=chatbot,
                index=index,
                seed=options["seed"],
                randomizer=randomizer,
            )
            existing_lead = (
                Lead.objects.filter(
                    chatbot=chatbot,
                    collected_fields__email=values["collected_fields"]["email"],
                    source="demo",
                )
                .order_by("created_at")
                .first()
            )

            if existing_lead is None:
                lead = Lead(chatbot=chatbot, source="demo")
                created_count += 1
            else:
                lead = existing_lead
                updated_count += 1

            for field_name, field_value in values.items():
                setattr(lead, field_name, field_value)
            lead.full_clean()
            lead.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo leads ready for '{chatbot.slug}': "
                f"{created_count} created, {updated_count} updated."
            )
        )

    @staticmethod
    def _ensure_demo_config(chatbot):
        config, _created = LeadCaptureConfig.objects.get_or_create(chatbot=chatbot)
        fields_by_value = {
            field["value"]: field for field in config.collectable_fields
        }
        demo_fields = (
            ("name", "Name", "text", "required"),
            ("email", "Email", "email", "required"),
            ("phone", "Phone", "text", "optional"),
            ("address", "Address", "text", "optional"),
            ("company", "Company", "text", "optional"),
            ("interest", "Interest", "text", "optional"),
            ("team_size", "Team Size", "text", "optional"),
        )
        for value, label, field_type, mode in demo_fields:
            fields_by_value[value] = {
                "label": label,
                "value": value,
                "mode": mode,
                "type": field_type,
            }
        config.collectable_fields = list(fields_by_value.values())
        config.full_clean()
        config.save(update_fields=["collectable_fields", "updated_at"])

    @staticmethod
    def _lead_values(*, chatbot, index, seed, randomizer):
        first_name = randomizer.choice(FIRST_NAMES)
        last_name = randomizer.choice(LAST_NAMES)
        country_code, city, address, phone_prefix = randomizer.choice(LOCATIONS)
        status = randomizer.choice(LeadStatusType.values)
        score_minimum, score_maximum = SCORE_RANGES[status]
        email_slug = f"{first_name}.{last_name}".lower()

        collected_fields = {
            "name": f"{first_name} {last_name}",
            "email": (
                f"{email_slug}+{chatbot.slug}-{seed}-{index:04d}@example.com"
            ),
            "phone": (
                f"{phone_prefix} {randomizer.randint(100, 999)} "
                f"{randomizer.randint(1000, 9999)}"
            ),
            "address": f"{address}, {city}",
            "company": randomizer.choice(COMPANIES),
            "interest": randomizer.choice(INTERESTS),
            "team_size": randomizer.choice(TEAM_SIZES),
        }
        return {
            "collected_fields": collected_fields,
            "initial_ip_address": f"198.51.100.{((index - 1) % 254) + 1}",
            "last_ip_address": f"203.0.113.{((index + seed - 1) % 254) + 1}",
            "detected_country_code": country_code,
            "detected_city": city,
            "status": status,
            "lead_score": randomizer.randint(score_minimum, score_maximum),
            "source": "demo",
        }
