from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from chatbot.models import (
    DEFAULT_CHATBOT_ESCALATION_RULE,
    DEFAULT_CHATBOT_FALLBACK_MESSAGE,
    DEFAULT_CHATBOT_NEVER_ANSWER,
    Chatbot,
    ChatbotConfig,
    ChatbotUser,
    ChatbotWidgetSettings,
)
from chatbot.services import assign_user_to_chatbot, create_chatbot
from chatbot.utils.choices import ChatbotRoleTypes
from subscription.models import ChatbotSubscription
from workspace.services import add_workspace_user, ensure_personal_workspace

User = get_user_model()


class ChatbotMembershipTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
        )
        self.member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
        )
        self.other_member = User.objects.create_user(
            email="other-member@example.com",
            password="StrongPass123!",
        )
        self.outsider = User.objects.create_user(
            email="outsider@example.com",
            password="StrongPass123!",
        )
        self.workspace = ensure_personal_workspace(self.owner)
        add_workspace_user(
            workspace=self.workspace,
            user=self.member,
            added_by=self.owner,
        )
        add_workspace_user(
            workspace=self.workspace,
            user=self.other_member,
            added_by=self.owner,
        )

    def test_chatbot_model_applies_default_conversation_messages(self):
        chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Direct Bot",
            created_by=self.owner,
        )

        self.assertEqual(
            chatbot.welcome_message,
            (
                "Hey, I am Direct Bot, I am here to answer anything you want "
                "to know about ."
            ),
        )
        self.assertEqual(
            chatbot.fallback_message,
            DEFAULT_CHATBOT_FALLBACK_MESSAGE,
        )
        self.assertEqual(
            chatbot.escalation_rule,
            DEFAULT_CHATBOT_ESCALATION_RULE,
        )
        self.assertEqual(chatbot.never_answer, DEFAULT_CHATBOT_NEVER_ANSWER)

    def test_chatbot_creator_is_assigned_as_admin(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )

        membership = ChatbotUser.objects.get(
            chatbot=chatbot,
            user=self.owner,
        )
        self.assertEqual(membership.role, ChatbotRoleTypes.ADMIN)
        self.assertTrue(
            ChatbotWidgetSettings.objects.filter(chatbot=chatbot).exists()
        )
        self.assertEqual(chatbot.chatbot_name, "Support Bot")
        self.assertEqual(chatbot.business_name, "")
        self.assertEqual(
            chatbot.welcome_message,
            (
                "Hey, I am Support Bot, I am here to answer anything you want "
                "to know about ."
            ),
        )
        self.assertEqual(
            chatbot.fallback_message,
            DEFAULT_CHATBOT_FALLBACK_MESSAGE,
        )
        self.assertEqual(
            chatbot.escalation_rule,
            DEFAULT_CHATBOT_ESCALATION_RULE,
        )
        self.assertEqual(chatbot.never_answer, DEFAULT_CHATBOT_NEVER_ANSWER)
        # Creating a chatbot no longer activates a subscription; the user
        # picks a plan from the pricing page afterwards.
        self.assertFalse(
            ChatbotSubscription.objects.filter(chatbot=chatbot).exists()
        )
        self.assertFalse(
            ChatbotConfig.objects.filter(chatbot=chatbot).exists()
        )

    def test_same_name_chatbots_can_coexist(self):
        first = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )
        second = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )

        self.assertNotEqual(first.slug, second.slug)
        self.assertEqual(
            Chatbot.objects.filter(
                workspace=self.workspace,
                chatbot_name="Support Bot",
            ).count(),
            2,
        )

    def test_workspace_member_can_assign_another_workspace_member(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )

        membership = assign_user_to_chatbot(
            chatbot=chatbot,
            user=self.other_member,
            assigned_by=self.member,
        )

        self.assertEqual(membership.user, self.other_member)
        self.assertTrue(membership.is_active)

    def test_outsider_cannot_be_assigned_to_chatbot(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )

        with self.assertRaises(PermissionDenied):
            assign_user_to_chatbot(
                chatbot=chatbot,
                user=self.outsider,
                assigned_by=self.owner,
            )

    def _subscribe_with_team_members_limit(self, chatbot, limit):
        from decimal import Decimal

        from subscription.choices import BillingInterval, PaymentProvider
        from subscription.models import PlanPrice, SubscriptionPlan
        from subscription.services.subscriptions import (
            activate_free_subscription,
        )

        plan = SubscriptionPlan.objects.create(
            name=f"Free {chatbot.slug}",
            is_free=True,
            team_members_limit=limit,
        )
        price = PlanPrice.objects.create(
            plan=plan,
            provider=PaymentProvider.MANUAL,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("0.00"),
        )
        activate_free_subscription(
            chatbot=chatbot,
            plan_price=price,
            user=self.owner,
        )

    def test_assignment_blocked_when_team_members_limit_reached(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )
        self._subscribe_with_team_members_limit(chatbot, 1)

        with self.assertRaises(ValidationError):
            assign_user_to_chatbot(
                chatbot=chatbot,
                user=self.other_member,
                assigned_by=self.owner,
            )
        self.assertFalse(
            ChatbotUser.objects.filter(
                chatbot=chatbot,
                user=self.other_member,
            ).exists()
        )

    def test_assignment_allowed_within_team_members_limit(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )
        self._subscribe_with_team_members_limit(chatbot, 2)

        membership = assign_user_to_chatbot(
            chatbot=chatbot,
            user=self.other_member,
            assigned_by=self.owner,
        )

        self.assertTrue(membership.is_active)
        self.assertEqual(
            ChatbotUser.objects.filter(
                chatbot=chatbot,
                is_active=True,
            ).count(),
            2,
        )

    def test_reactivating_member_beyond_limit_is_blocked(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )
        self._subscribe_with_team_members_limit(chatbot, 2)
        assign_user_to_chatbot(
            chatbot=chatbot,
            user=self.other_member,
            assigned_by=self.owner,
        )
        ChatbotUser.objects.filter(
            chatbot=chatbot,
            user=self.other_member,
        ).update(is_active=False)
        self._subscribe_with_team_members_limit(chatbot, 1)

        with self.assertRaises(ValidationError):
            assign_user_to_chatbot(
                chatbot=chatbot,
                user=self.other_member,
                assigned_by=self.owner,
            )

    def test_unlimited_team_members_plan_allows_assignment(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )
        self._subscribe_with_team_members_limit(chatbot, None)
        assign_user_to_chatbot(
            chatbot=chatbot,
            user=self.member,
            assigned_by=self.owner,
        )

        membership = assign_user_to_chatbot(
            chatbot=chatbot,
            user=self.other_member,
            assigned_by=self.owner,
        )

        self.assertTrue(membership.is_active)
