from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    RenewalMode,
    SubscriptionStatus,
)
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
        )
        self.client.force_authenticate(self.user)

    @patch(
        "subscription.services.subscriptions.StripeBillingService.retrieve_checkout_session"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService.create_checkout_session"
    )
    def test_chatbot_admin_can_start_and_reuse_checkout(
        self,
        create_checkout,
        retrieve_checkout,
    ):
        create_checkout.return_value = {
            "id": "cs_test_123",
            "client_secret": "cs_test_123_secret_example",
            "status": "open",
        }
        retrieve_checkout.return_value = {
            "id": "cs_test_123",
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
        self.assertEqual(
            first.data["data"]["client_secret"],
            "cs_test_123_secret_example",
        )
        self.assertEqual(ChatbotSubscription.objects.count(), 1)
        create_checkout.assert_called_once()
        retrieve_checkout.assert_called_once_with(session_id="cs_test_123")

    @patch(
        "subscription.services.subscriptions.StripeBillingService.create_checkout_session"
    )
    def test_disabled_overage_ignores_a_stale_price_unit(self, create_checkout):
        self.price.ai_message_overage_unit_price = Decimal("0.01")
        self.price.save(update_fields=["ai_message_overage_unit_price", "updated_at"])
        create_checkout.return_value = {
            "id": "cs_test_stale_overage",
            "client_secret": "cs_test_stale_overage_secret",
            "status": "open",
        }

        response = self.client.post(
            f'{reverse("subscription-checkout")}?chatbot={self.chatbot.slug}',
            {"plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        subscription = ChatbotSubscription.objects.get()
        self.assertFalse(subscription.snapshot["overage"]["enabled"])
        self.assertIsNone(subscription.snapshot["overage"]["unit_price"])

    @patch(
        "subscription.services.subscriptions.StripeBillingService.retrieve_checkout_session"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService.create_checkout_session"
    )
    def test_expired_checkout_is_replaced_with_a_new_session(
        self,
        create_checkout,
        retrieve_checkout,
    ):
        create_checkout.side_effect = [
            {
                "id": "cs_test_expired",
                "client_secret": "cs_test_expired_secret",
                "status": "open",
            },
            {
                "id": "cs_test_replacement",
                "client_secret": "cs_test_replacement_secret",
                "status": "open",
            },
        ]
        retrieve_checkout.return_value = {
            "id": "cs_test_expired",
            "status": "expired",
        }
        url = f'{reverse("subscription-checkout")}?chatbot={self.chatbot.slug}'

        first = self.client.post(
            url,
            {"plan_price_id": str(self.price.id)},
            format="json",
        )
        replacement = self.client.post(
            url,
            {"plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(replacement.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            replacement.data["data"]["client_secret"],
            "cs_test_replacement_secret",
        )
        self.assertFalse(replacement.data["data"]["reused"])
        self.assertEqual(ChatbotSubscription.objects.count(), 1)
        self.assertEqual(create_checkout.call_count, 2)

    @patch(
        "subscription.services.subscriptions.StripeBillingService.retrieve_checkout_session"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService.create_checkout_session"
    )
    def test_paid_completed_checkout_is_reconciled_without_a_409(
        self,
        create_checkout,
        retrieve_checkout,
    ):
        create_checkout.return_value = {
            "id": "cs_test_complete",
            "client_secret": "cs_test_complete_secret",
            "status": "open",
        }
        retrieve_checkout.return_value = {
            "id": "cs_test_complete",
            "status": "complete",
            "payment_status": "paid",
            "customer": "cus_123",
            "subscription": "sub_123",
        }
        url = f'{reverse("subscription-checkout")}?chatbot={self.chatbot.slug}'

        self.client.post(
            url,
            {"plan_price_id": str(self.price.id)},
            format="json",
        )
        reconciled = self.client.post(
            url,
            {"plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(reconciled.status_code, status.HTTP_200_OK)
        self.assertEqual(
            reconciled.data["data"]["action"],
            "subscription_activated",
        )
        self.assertFalse(reconciled.data["data"]["requires_checkout"])
        self.assertIsNone(reconciled.data["data"]["client_secret"])
        subscription = ChatbotSubscription.objects.get()
        self.assertEqual(subscription.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(subscription.provider_subscription_id, "sub_123")

    @patch(
        "subscription.services.subscriptions.StripeBillingService.change_subscription_plan"
    )
    def test_active_stripe_subscription_can_change_plan(self, change_plan):
        current = ChatbotSubscription.objects.create(
            chatbot=self.chatbot,
            plan_price=self.price,
            selected_by=self.user,
            provider=PaymentProvider.STRIPE,
            renewal_mode=RenewalMode.PROVIDER_MANAGED,
            status=SubscriptionStatus.ACTIVE,
            provider_customer_id="cus_123",
            provider_subscription_id="sub_123",
        )
        premium = SubscriptionPlan.objects.create(
            name="Premium",
            ai_message_limit=3500,
        )
        premium_price = PlanPrice.objects.create(
            plan=premium,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("79.00"),
        )
        change_plan.return_value = {
            "id": "sub_123",
            "status": "active",
            "customer": "cus_123",
            "cancel_at_period_end": False,
            "latest_invoice": "in_upgrade",
            "items": {"data": []},
        }

        response = self.client.post(
            f'{reverse("subscription-checkout")}?chatbot={self.chatbot.slug}',
            {"plan_price_id": str(premium_price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["action"], "plan_changed")
        self.assertFalse(response.data["data"]["requires_checkout"])
        current.refresh_from_db()
        self.assertEqual(current.status, SubscriptionStatus.CANCELED)
        replacement = ChatbotSubscription.objects.get(
            chatbot=self.chatbot,
            status=SubscriptionStatus.ACTIVE,
        )
        self.assertEqual(replacement.plan_price, premium_price)
        self.assertEqual(replacement.provider_subscription_id, "sub_123")
        change_plan.assert_called_once()

    @patch(
        "subscription.services.subscriptions.StripeBillingService.cancel_subscription"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService.retrieve_checkout_session"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService.create_checkout_session"
    )
    def test_failed_async_checkout_can_start_a_fresh_checkout(
        self,
        create_checkout,
        retrieve_checkout,
        cancel_subscription,
    ):
        failed = ChatbotSubscription.objects.create(
            chatbot=self.chatbot,
            plan_price=self.price,
            selected_by=self.user,
            provider=PaymentProvider.STRIPE,
            renewal_mode=RenewalMode.PROVIDER_MANAGED,
            status=SubscriptionStatus.INCOMPLETE,
            provider_customer_id="cus_123",
            provider_subscription_id="sub_failed",
            provider_metadata={
                "checkout_session_id": "cs_test_failed",
                "checkout_client_secret": "cs_test_failed_secret",
                "checkout_status": "complete",
                "checkout_payment_status": "failed",
                "checkout_async_payment_failed": True,
            },
        )
        retrieve_checkout.return_value = {
            "id": "cs_test_failed",
            "status": "complete",
            "payment_status": "unpaid",
            "customer": "cus_123",
            "subscription": "sub_failed",
        }
        cancel_subscription.return_value = {
            "id": "sub_failed",
            "status": "canceled",
        }
        create_checkout.return_value = {
            "id": "cs_test_retry",
            "client_secret": "cs_test_retry_secret",
            "status": "open",
        }

        response = self.client.post(
            f'{reverse("subscription-checkout")}?chatbot={self.chatbot.slug}',
            {"plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["action"], "checkout")
        self.assertEqual(
            response.data["data"]["client_secret"],
            "cs_test_retry_secret",
        )
        failed.refresh_from_db()
        self.assertEqual(failed.status, SubscriptionStatus.CANCELED)
        self.assertEqual(ChatbotSubscription.objects.count(), 2)
        cancel_subscription.assert_called_once_with(
            subscription_id="sub_failed"
        )

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
            "client_secret": "cs_test_upgrade_secret_example",
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
