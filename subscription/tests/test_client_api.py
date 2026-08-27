from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from subscription.choices import BillingInterval, PaymentProvider
from subscription.models import ChatbotSubscription, PlanPrice, SubscriptionPlan
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser


class SubscriptionClientAPITests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="owner@example.com",
            password="strong-password",
        )
        self.workspace = Workspace.objects.create(
            name="Example Workspace",
            owner=self.user,
            created_by=self.user,
            updated_by=self.user,
        )
        WorkspaceUser.objects.create(
            workspace=self.workspace,
            user=self.user,
            role=WorkspaceRole.ADMIN,
            created_by=self.user,
            updated_by=self.user,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            created_by=self.user,
            updated_by=self.user,
        )
        ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=self.user,
            role=ChatbotRoleTypes.ADMIN,
        )
        self.plan = SubscriptionPlan.objects.create(
            name="Growth",
            ai_message_limit=1000,
            file_size_limit_mb=25,
            knowledge_chunk_limit=5000,
            features=["knowledge_base"],
        )
        self.price = PlanPrice.objects.create(
            plan=self.plan,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("19.00"),
            provider_price_id="price_growth_monthly",
        )
        self.client.force_authenticate(self.user)

    @patch(
        "subscription.services.subscriptions.StripeBillingService.create_checkout_session"
    )
    def test_chatbot_admin_can_start_and_reuse_checkout(self, create_checkout):
        create_checkout.return_value = {
            "id": "cs_test_123",
            "url": "https://checkout.stripe.test/session",
            "status": "open",
        }
        url = f'{reverse("subscription-checkout")}?chatbot={self.chatbot.slug}'

        first = self.client.post(
            url,
            {"plan_price_id": str(self.price.id)},
            format="json",
        )
        second = self.client.post(
            url,
            {"plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertFalse(first.data["data"]["reused"])
        self.assertTrue(second.data["data"]["reused"])
        self.assertEqual(ChatbotSubscription.objects.count(), 1)
        create_checkout.assert_called_once()

    def test_public_plan_list_does_not_expose_stripe_price_identifiers(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(reverse("subscription-plan-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        price = response.data["data"][0]["prices"][0]
        self.assertNotIn("provider_price_id", price)
        self.assertEqual(price["amount"], "19.00")

    def test_free_plan_can_be_activated_without_stripe(self):
        free_plan = SubscriptionPlan.objects.create(name="Free", is_free=True)
        free_price = PlanPrice.objects.create(
            plan=free_plan,
            provider=PaymentProvider.MANUAL,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("0"),
        )

        response = self.client.post(
            f'{reverse("subscription-activate-free")}?chatbot={self.chatbot.slug}',
            {"plan_price_id": str(free_price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["status"], "active")

    @patch(
        "subscription.services.subscriptions.StripeBillingService.create_checkout_session"
    )
    def test_paid_checkout_replaces_an_active_free_subscription(self, create_checkout):
        free_plan = SubscriptionPlan.objects.create(name="Free", is_free=True)
        free_price = PlanPrice.objects.create(
            plan=free_plan,
            provider=PaymentProvider.MANUAL,
            billing_interval=BillingInterval.MONTHLY,
            amount=Decimal("0"),
        )
        self.client.post(
            f'{reverse("subscription-activate-free")}?chatbot={self.chatbot.slug}',
            {"plan_price_id": str(free_price.id)},
            format="json",
        )
        create_checkout.return_value = {
            "id": "cs_test_upgrade",
            "url": "https://checkout.stripe.test/upgrade",
            "status": "open",
        }

        response = self.client.post(
            f'{reverse("subscription-checkout")}?chatbot={self.chatbot.slug}',
            {"plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            ChatbotSubscription.objects.filter(
                chatbot=self.chatbot,
                status="canceled",
            ).count(),
            1,
        )

    def test_non_admin_chatbot_member_cannot_checkout(self):
        member = get_user_model().objects.create_user(
            email="member@example.com",
            password="strong-password",
        )
        WorkspaceUser.objects.create(
            workspace=self.workspace,
            user=member,
            role=WorkspaceRole.MEMBER,
        )
        ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=member,
            role=ChatbotRoleTypes.MEMBER,
        )
        self.client.force_authenticate(member)

        response = self.client.post(
            f'{reverse("subscription-checkout")}?chatbot={self.chatbot.slug}',
            {"plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
