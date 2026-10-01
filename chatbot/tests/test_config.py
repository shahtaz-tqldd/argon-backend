from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from django.db.migrations.loader import MigrationLoader
from django.db.migrations.writer import MigrationWriter
from django.db.migrations.operations.models import RenameModel
from django.test import SimpleTestCase

from chatbot.models import Chatbot, ChatbotConfig
from subscription.choices import RenewalMode, SubscriptionStatus
from subscription.models import ChatbotSubscription
from subscription.services.subscriptions import OPEN_SUBSCRIPTION_STATUSES


class ChatbotConfigTests(SimpleTestCase):
    def setUp(self):
        self.chatbot = Chatbot(chatbot_name="Support")
        self.config = ChatbotConfig(chatbot=self.chatbot)
        self.subscription = ChatbotSubscription(
            chatbot=self.chatbot,
            status=SubscriptionStatus.ACTIVE,
            renewal_mode=RenewalMode.APP_MANAGED,
            next_billing_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            current_period_end=datetime(2026, 11, 2, tzinfo=timezone.utc),
            snapshot={
                "plan": {"name": "Subscribed plan", "is_free": False},
                "pricing": {"billing_interval": "monthly", "amount": "29.99", "currency": "USD"},
            },
        )

    def summary(self, subscription):
        with patch.object(ChatbotSubscription.objects, "filter") as query:
            query.return_value.first.return_value = subscription
            result = self.config.current_subscription()
            query.assert_called_once_with(
                chatbot_id=self.chatbot.id,
                status__in=OPEN_SUBSCRIPTION_STATUSES,
            )
        return result

    def test_reads_immutable_pricing_without_fetching_live_plan(self):
        self.assertEqual(self.summary(self.subscription), {
            "plan_name": "Subscribed plan",
            "billing_cycle": "monthly",
            "price": Decimal("29.99"),
            "currency": "USD",
            "next_billing_at": self.subscription.next_billing_at,
        })

    def test_provider_managed_uses_period_end_when_no_next_billing_date(self):
        self.subscription.next_billing_at = None
        self.subscription.renewal_mode = RenewalMode.PROVIDER_MANAGED
        self.assertEqual(
            self.summary(self.subscription)["next_billing_at"],
            self.subscription.current_period_end,
        )

    def test_free_or_canceling_subscription_has_no_next_charge(self):
        self.subscription.cancel_at_period_end = True
        self.assertIsNone(self.summary(self.subscription)["next_billing_at"])
        self.subscription.cancel_at_period_end = False
        self.subscription.snapshot["plan"]["is_free"] = True
        self.assertIsNone(self.summary(self.subscription)["next_billing_at"])

    def test_manual_billing_does_not_invent_renewal_date(self):
        self.subscription.next_billing_at = None
        self.subscription.renewal_mode = RenewalMode.MANUAL
        self.assertIsNone(self.summary(self.subscription)["next_billing_at"])

    def test_no_current_subscription_returns_none(self):
        self.assertIsNone(self.summary(None))

    def test_migration_renames_existing_model_and_preserves_all_fields(self):
        loader = MigrationLoader(None)
        before = loader.project_state([("chatbot", "0016_chatbotactivitylog_module")])
        after = loader.project_state([("chatbot", "0017_rename_chatbotcapacity_chatbotconfig")])
        previous = before.models[("chatbot", "chatbotcapacity")]
        updated = after.models[("chatbot", "chatbotconfig")]
        self.assertNotIn(("chatbot", "chatbotcapacity"), after.models)
        self.assertEqual(set(previous.fields), set(updated.fields))
        for field in previous.fields:
            self.assertEqual(
                MigrationWriter.serialize(previous.fields[field]),
                MigrationWriter.serialize(updated.fields[field]),
            )
        migration = loader.get_migration("chatbot", "0017_rename_chatbotcapacity_chatbotconfig")
        self.assertIsInstance(migration.operations[0], RenameModel)
        self.assertEqual(updated.fields["chatbot"].remote_field.related_name, "capacity")


class ChatbotBaseSubscriptionSerializerTests(SimpleTestCase):
    def test_subscription_summary_comes_from_config(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from django.utils.dateparse import parse_datetime
        from chatbot.api.v1.client.serializers import ChatbotBaseResponseSerializer

        next_billing_at = datetime(2026, 11, 1, tzinfo=timezone.utc)
        config = Mock()
        config.current_subscription.return_value = {
            "plan_name": "Pro",
            "billing_cycle": "monthly",
            "price": Decimal("29.99"),
            "currency": "USD",
            "next_billing_at": next_billing_at,
        }
        result = ChatbotBaseResponseSerializer().get_current_subscription_plan(
            SimpleNamespace(capacity=config),
        )
        config.current_subscription.assert_called_once_with()
        self.assertEqual(set(result), {
            "plan_name", "billing_cycle", "price", "currency", "next_billing_at",
        })
        self.assertEqual(result["plan_name"], "Pro")
        self.assertEqual(result["billing_cycle"], "monthly")
        self.assertEqual(result["price"], "29.99")
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(parse_datetime(result["next_billing_at"]), next_billing_at)

    def test_no_config_or_subscription_returns_null(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from chatbot.api.v1.client.serializers import ChatbotBaseResponseSerializer

        serializer = ChatbotBaseResponseSerializer()
        self.assertIsNone(serializer.get_current_subscription_plan(SimpleNamespace()))
        config = Mock()
        config.current_subscription.return_value = None
        self.assertIsNone(serializer.get_current_subscription_plan(SimpleNamespace(capacity=config)))
