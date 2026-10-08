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
from workspace.services import ensure_personal_workspace

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
        self.workspace = ensure_personal_workspace(self.owner)

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

    def test_same_name_in_same_workspace_is_rejected(self):
        create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )

        with self.assertRaises(ValidationError) as exc:
            create_chatbot(
                workspace=self.workspace,
                chatbot_name="Support Bot",
                created_by=self.owner,
            )
        self.assertIn(
            "already exists in this workspace",
            str(exc.exception),
        )
        self.assertEqual(
            Chatbot.objects.filter(
                workspace=self.workspace,
                chatbot_name="Support Bot",
            ).count(),
            1,
        )

    def test_same_name_matching_different_case_is_rejected(self):
        create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )

        with self.assertRaises(ValidationError):
            create_chatbot(
                workspace=self.workspace,
                chatbot_name="SUPPORT BOT",
                created_by=self.owner,
            )

    def test_same_name_in_different_workspaces_can_coexist(self):
        other_owner = User.objects.create_user(
            email="other-owner@example.com",
            password="StrongPass123!",
        )
        other_workspace = ensure_personal_workspace(other_owner)

        first = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )
        second = create_chatbot(
            workspace=other_workspace,
            chatbot_name="Support Bot",
            created_by=other_owner,
        )

        self.assertNotEqual(first.slug, second.slug)
        self.assertEqual(
            Chatbot.objects.filter(chatbot_name="Support Bot").count(),
            2,
        )

    def test_soft_deleted_chatbot_frees_its_name_in_the_workspace(self):
        first = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )
        first.is_deleted = True
        first.save(update_fields=["is_deleted", "updated_at"])

        second = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )

        self.assertNotEqual(first.pk, second.pk)

    def test_database_blocks_same_active_name_in_one_workspace(self):
        from django.db import IntegrityError, transaction

        create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )
        duplicate = Chatbot(
            workspace=self.workspace,
            chatbot_name="support bot",
            created_by=self.owner,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_owner_can_assign_any_active_user(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )

        membership = assign_user_to_chatbot(
            chatbot=chatbot,
            user=self.other_member,
            assigned_by=self.owner,
        )

        self.assertEqual(membership.user, self.other_member)
        self.assertTrue(membership.is_active)

    def test_non_owner_cannot_assign_users(self):
        chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.owner,
        )

        with self.assertRaises(PermissionDenied):
            assign_user_to_chatbot(
                chatbot=chatbot,
                user=self.other_member,
                assigned_by=self.member,
            )

    def _subscribe_with_team_members_limit(self, chatbot, limit):
        from decimal import Decimal

        from subscription.utils.choices import BillingInterval, PaymentProvider
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
