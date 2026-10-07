import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from chat.models import ChatSession, ChatSessionTakeover
from chat.services.messages import create_chat_message, create_system_message
from chat.utils.choices import (
    ChatMessageSenderType,
    ChatSessionChannel,
    ChatSessionStatus,
    ChatSessionTakeoverReleaseReason,
)
from chatbot.models import Chatbot, ChatbotUser, ChatbotVisitor
from lead_capture.models import Lead, LeadCaptureConfig
from lead_capture.services.signals import record_lead_signal
from lead_capture.utils.choices import LeadStatusType


FIRST_NAMES = (
    "Aisha",
    "Arjun",
    "Daniel",
    "Elena",
    "Hasan",
    "Maya",
    "Noah",
    "Olivia",
    "Priya",
    "Rafi",
    "Sofia",
    "Tariq",
)

LAST_NAMES = (
    "Ahmed",
    "Chen",
    "Davis",
    "Garcia",
    "Khan",
    "Kim",
    "Miller",
    "Patel",
    "Rahman",
    "Silva",
    "Smith",
    "Taylor",
)

COMPANIES = (
    "Acme Labs",
    "Bluebird Analytics",
    "Cloudline Systems",
    "Evergreen Commerce",
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

# Each blueprint scripts one demo conversation. ``agent_role`` decides the
# human-ownership shape: "none" (AI-only), "active" (open session owned by
# an agent), "resolved", or "closed". Script kinds are "visitor", "ai",
# "agent", and "system:<event_type>".
SESSION_BLUEPRINTS = (
    {
        "topic": "pricing",
        "lead_status": LeadStatusType.NEW,
        "agent_role": "none",
        "requires_attention": True,
        "attention_reason": (
            "Visitor asked to discuss annual billing with a human agent."
        ),
        "signal_score": 55,
        "signal_summary": (
            "Visitor is comparing plans and asked about annual billing."
        ),
        "script": (
            (
                "visitor",
                "Hi! I'm comparing plans for our support team. What does "
                "the Growth plan include?",
            ),
            (
                "ai",
                "Hi! The Growth plan covers 3 chatbots, 10,000 AI replies "
                "per month, and human handoff. May I have your name and "
                "email so I can send the full pricing sheet?",
            ),
            (
                "visitor",
                "Sure — I just filled in my details in the form.",
            ),
            (
                "ai",
                "Thanks {first_name}! I've noted your interest in "
                "{interest}. Would you like a teammate to walk you through "
                "annual billing?",
            ),
            (
                "visitor",
                "Yes please, I'd like to talk to someone about annual "
                "billing.",
            ),
            (
                "ai",
                "Absolutely — I've flagged this chat for our team and "
                "someone will join shortly.",
            ),
            (
                "system:session.attention_requested",
                "Human attention requested: visitor asked about annual "
                "billing options.",
            ),
        ),
    },
    {
        "topic": "integrations",
        "lead_status": LeadStatusType.QUALIFIED,
        "agent_role": "active",
        "requires_attention": False,
        "signal_score": 72,
        "signal_summary": (
            "Visitor confirmed a {team_size} team and an active Slack and "
            "HubSpot stack."
        ),
        "script": (
            (
                "visitor",
                "Hey, does the widget integrate with Slack and HubSpot?",
            ),
            (
                "ai",
                "Yes — Slack notifications and HubSpot contact sync are "
                "included on paid plans. What does your current stack look "
                "like?",
            ),
            (
                "visitor",
                "Mostly Slack, HubSpot, and Notion. We're a {team_size} "
                "team at {company}.",
            ),
            (
                "ai",
                "Great fit. I'll connect you with a specialist to scope "
                "the setup — one moment.",
            ),
            (
                "system:session.taken_over",
                "{agent_name} took over the conversation.",
            ),
            (
                "agent",
                "Hi {first_name}, this is {agent_name}. I can configure "
                "Slack and HubSpot for you today. Which Slack workspace "
                "should I use?",
            ),
            (
                "visitor",
                "{company_slug}.slack.com — thanks!",
            ),
            (
                "agent",
                "Perfect. I'm sending the setup checklist to {email} now.",
            ),
        ),
    },
    {
        "topic": "product_demo",
        "lead_status": LeadStatusType.CONTACTED,
        "agent_role": "resolved",
        "requires_attention": False,
        "signal_score": 85,
        "signal_summary": (
            "Visitor booked a product demo while evaluating vendors this "
            "quarter."
        ),
        "script": (
            (
                "visitor",
                "Hello, I'd like a product demo for our sales team.",
            ),
            (
                "ai",
                "Happy to help! I can book a 30-minute demo. Could you "
                "share your name and work email?",
            ),
            (
                "visitor",
                "{name}, {email}. We're evaluating vendors this quarter.",
            ),
            (
                "ai",
                "Thanks, {first_name}! Booking you with our solutions "
                "team now.",
            ),
            (
                "system:session.taken_over",
                "{agent_name} took over the conversation.",
            ),
            (
                "agent",
                "Hi {first_name}, I run demos for teams like {company}. "
                "I've sent a calendar invite for tomorrow at 3:00 pm — "
                "does that work?",
            ),
            ("visitor", "That works perfectly."),
            ("agent", "Great. Anything specific you'd like covered?"),
            ("visitor", "Bulk usage and the reporting dashboard, mostly."),
            (
                "system:session.resolved",
                "Session resolved by {agent_name}.",
            ),
        ),
    },
    {
        "topic": "support",
        "lead_status": LeadStatusType.NEW,
        "agent_role": "closed",
        "requires_attention": False,
        "signal_score": 45,
        "signal_summary": (
            "Visitor reported a blocking widget issue but remains an "
            "active user."
        ),
        "script": (
            (
                "visitor",
                "Our widget stopped loading on our site yesterday.",
            ),
            (
                "ai",
                "Sorry about that! Could you share the page URL where the "
                "widget fails to load?",
            ),
            (
                "visitor",
                "https://{company_slug}.example.com/pricing — the console "
                "shows a CORS error.",
            ),
            (
                "ai",
                "Thanks — that looks like a missing allowed origin. "
                "Bringing in a support engineer.",
            ),
            (
                "system:session.taken_over",
                "{agent_name} took over the conversation.",
            ),
            (
                "agent",
                "Hi {first_name}, I've added {company_slug}.example.com "
                "to your allowed origins. Could you reload and confirm?",
            ),
            ("visitor", "It's loading again. Thank you!"),
            (
                "agent",
                "You're welcome — closing this out. Ping us if it recurs.",
            ),
            (
                "system:session.closed",
                "Session closed by {agent_name}.",
            ),
        ),
    },
    {
        "topic": "enterprise",
        "lead_status": LeadStatusType.CONVERTED,
        "agent_role": "none",
        "requires_attention": False,
        "signal_score": 92,
        "signal_summary": (
            "Enterprise visitor with a 300-seat requirement, SSO needs, "
            "and a Q1 rollout timeline."
        ),
        "script": (
            (
                "visitor",
                "We need chatbots across four regional sites with SSO. Do "
                "you support SAML?",
            ),
            (
                "ai",
                "Yes — SAML and OIDC SSO ship with the Enterprise plan, "
                "along with regional data residency. How many seats would "
                "you need?",
            ),
            (
                "visitor",
                "Around 300 seats, rolling out in Q1. I'm {name} — {email}.",
            ),
            (
                "ai",
                "Thanks, {first_name}! For 300 seats I'd recommend "
                "Enterprise with a dedicated success manager. Our team "
                "will follow up with a quote.",
            ),
        ),
    },
)


class Command(BaseCommand):
    help = (
        "Create demo chat sessions for a chatbot: demo visitors, "
        "visitor/AI/agent messages, leads, and lead signals."
    )

    # python manage.py create_example_chat_session --bot-id <chatbot-id-or-slug>

    def add_arguments(self, parser):
        parser.add_argument(
            "--bot-id",
            required=True,
            help="ID or slug of the chatbot to create demo sessions against.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=5,
            help="Number of demo chat sessions to create. Defaults to 5.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=1,
            help=(
                "Seed used for repeatable data. Reusing it updates the same "
                "demo sessions. Defaults to 1."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        count = options["count"]
        if count < 1:
            raise CommandError("--count must be at least 1.")
        if count > 100:
            raise CommandError("--count cannot be greater than 100.")

        chatbot = self.get_chatbot(options["bot_id"])
        self._agent = None
        self._ensure_lead_capture_config(chatbot)

        randomizer = random.Random(options["seed"])
        created_count = 0
        updated_count = 0
        for index in range(1, count + 1):
            blueprint = SESSION_BLUEPRINTS[(index - 1) % len(SESSION_BLUEPRINTS)]
            _session, is_new = self._create_demo_session(
                chatbot=chatbot,
                blueprint=blueprint,
                index=index,
                seed=options["seed"],
                randomizer=randomizer,
            )
            created_count += int(is_new)
            updated_count += int(not is_new)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo chat sessions ready for '{chatbot.slug}': "
                f"{created_count} created, {updated_count} updated."
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

    def get_agent(self, chatbot):
        if self._agent is None:
            self._agent = (
                ChatbotUser.objects.filter(
                    chatbot=chatbot,
                    is_active=True,
                    user__is_active=True,
                )
                .select_related("user")
                .order_by("created_at")
                .first()
            )
            if self._agent is None:
                raise CommandError(
                    f"Chatbot '{chatbot.slug}' has no active human agents. "
                    "Add a chatbot member before creating agent demo "
                    "messages."
                )
        return self._agent

    @staticmethod
    def _ensure_lead_capture_config(chatbot):
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

    def _create_demo_session(self, *, chatbot, blueprint, index, seed, randomizer):
        first_name = randomizer.choice(FIRST_NAMES)
        last_name = randomizer.choice(LAST_NAMES)
        country_code, city, address, phone_prefix = randomizer.choice(LOCATIONS)
        company = randomizer.choice(COMPANIES)
        interest = randomizer.choice(INTERESTS)
        team_size = randomizer.choice(TEAM_SIZES)

        name = f"{first_name} {last_name}"
        email = (
            f"{first_name}.{last_name}+{chatbot.slug}-session-"
            f"{seed}-{index:04d}@example.com"
        ).lower()
        collected_fields = {
            "name": name,
            "email": email,
            "phone": (
                f"{phone_prefix} {randomizer.randint(100, 999)} "
                f"{randomizer.randint(1000, 9999)}"
            ),
            "address": f"{address}, {city}",
            "company": company,
            "interest": interest,
            "team_size": team_size,
        }

        lead = (
            Lead.objects.filter(
                chatbot=chatbot,
                collected_fields__email=email,
                source="demo",
            )
            .order_by("created_at")
            .first()
        )
        if lead is None:
            lead = Lead(chatbot=chatbot, source="demo")
        lead.collected_fields = collected_fields
        lead.status = blueprint["lead_status"]
        lead.full_clean()
        lead.save()

        visitor, _created = ChatbotVisitor.objects.get_or_create(
            chatbot=chatbot,
            visitor_id=f"demo-{chatbot.slug}-{seed}-{index:04d}",
        )
        visitor.lead = lead
        visitor.ip_address = f"203.0.113.{(index % 250) + 1}"
        visitor.detected_location = f"{city}, {country_code}"
        visitor.detected_country = country_code
        visitor.metadata = {
            "demo": True,
            "browser": "Chrome 129",
            "locale": "en-US",
            "timezone": "UTC",
        }
        visitor.full_clean()
        visitor.save()

        session = visitor.chat_sessions.order_by("created_at").first()
        if session is None:
            session = ChatSession(chatbot=chatbot, visitor=visitor)
            is_new = True
        else:
            session.messages.all().delete()
            lead.signals.all().delete()
            ChatSessionTakeover.objects.filter(chat_session=session).delete()
            is_new = False

        session.lead = lead
        session.channel = ChatSessionChannel.WEB_WIDGET
        session.status = ChatSessionStatus.OPEN
        session.ai_enabled = True
        session.assigned_to = None
        session.requires_attention = False
        session.attention_reason = ""
        session.attention_requested_at = None
        session.resolved_at = None
        session.closed_at = None
        session.metadata = {
            "demo": True,
            "seed": seed,
            "topic": blueprint["topic"],
        }
        session.full_clean()
        session.save()

        agent = None
        if blueprint["agent_role"] != "none":
            agent = self.get_agent(chatbot)

        context = {
            "first_name": first_name,
            "name": name,
            "email": email,
            "company": company,
            "company_slug": slugify(company),
            "interest": interest,
            "team_size": team_size,
            "city": city,
            "agent_name": (agent.user.name or "Support Agent") if agent else "",
        }

        takeover = None
        signal_message = None
        for kind, template in blueprint["script"]:
            content = template.format(**context)
            if kind == "visitor":
                create_chat_message(
                    session,
                    sender_type=ChatMessageSenderType.VISITOR,
                    content=content,
                )
            elif kind == "ai":
                message = create_chat_message(
                    session,
                    sender_type=ChatMessageSenderType.AI,
                    content=content,
                )
                if signal_message is None:
                    signal_message = message
            elif kind == "agent":
                create_chat_message(
                    session,
                    sender_type=ChatMessageSenderType.AGENT,
                    sender=agent,
                    content=content,
                )
            elif kind.startswith("system:"):
                event_type = kind.split(":", 1)[1]
                if event_type == "session.taken_over":
                    takeover = self._take_over(session, agent)
                create_system_message(
                    session,
                    content=content,
                    event_type=event_type,
                    visibility=(
                        "internal"
                        if event_type == "session.attention_requested"
                        else "public"
                    ),
                )

        if blueprint["requires_attention"]:
            session.requires_attention = True
            session.attention_reason = blueprint["attention_reason"]
            session.attention_requested_at = timezone.now()
            session.save(
                update_fields=[
                    "requires_attention",
                    "attention_reason",
                    "attention_requested_at",
                    "updated_at",
                ]
            )

        self._release_takeover(session, takeover, blueprint["agent_role"])

        record_lead_signal(
            lead,
            score=blueprint["signal_score"],
            summary=blueprint["signal_summary"].format(**context),
            message=signal_message,
        )
        return session, is_new

    @staticmethod
    def _take_over(session, agent):
        takeover = ChatSessionTakeover(chat_session=session, agent=agent)
        takeover.full_clean()
        takeover.save()
        session.assigned_to = agent
        session.ai_enabled = False
        session.save(
            update_fields=["assigned_to", "ai_enabled", "updated_at"]
        )
        return takeover

    @staticmethod
    def _release_takeover(session, takeover, agent_role):
        if takeover is None or agent_role not in ("resolved", "closed"):
            return
        now = timezone.now()
        takeover.released_at = now
        takeover.release_reason = (
            ChatSessionTakeoverReleaseReason.RESOLVED
            if agent_role == "resolved"
            else ChatSessionTakeoverReleaseReason.CLOSED
        )
        takeover.full_clean()
        takeover.save(
            update_fields=["released_at", "release_reason", "updated_at"]
        )
        session.status = (
            ChatSessionStatus.RESOLVED
            if agent_role == "resolved"
            else ChatSessionStatus.CLOSED
        )
        session.resolved_at = now if agent_role == "resolved" else None
        session.closed_at = now if agent_role == "closed" else None
        session.assigned_to = None
        session.ai_enabled = True
        session.save(
            update_fields=[
                "status",
                "resolved_at",
                "closed_at",
                "assigned_to",
                "ai_enabled",
                "updated_at",
            ]
        )
