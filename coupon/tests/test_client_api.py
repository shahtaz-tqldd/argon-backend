from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from coupon.models import Coupon, CouponRedemption, Discount
from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription, PlanPrice, SubscriptionPlan
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser


class CouponClientAPITests(APITestCase):
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
        )
        self.price = PlanPrice.objects.create(
            plan=self.plan,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("19.00"),
        )
        percentage = Discount.objects.create(
            name="Launch 10%",
            discount_type="percentage",
            value=Decimal("10.00"),
        )
        self.percent_coupon = Coupon.objects.create(
            code="SAVE10",
            discount=percentage,
            created_by=self.user,
        )
        fixed = Discount.objects.create(
            name="Five off",
            discount_type="fixed_amount",
            value=Decimal("5.00"),
            currency="USD",
        )
        self.fixed_coupon = Coupon.objects.create(
            code="FIVEUSD",
            discount=fixed,
            created_by=self.user,
        )
        self.client.force_authenticate(self.user)
        self.apply_url = f'{reverse("coupon-apply")}?chatbot={self.chatbot.slug}'
        self.current_url = f'{reverse("coupon-current")}?chatbot={self.chatbot.slug}'
        self.remove_url = f'{reverse("coupon-remove")}?chatbot={self.chatbot.slug}'
        self.checkout_url = (
            f'{reverse("subscription-checkout")}?chatbot={self.chatbot.slug}'
        )

    def create_incomplete_subscription(self):
        return ChatbotSubscription.objects.create(
            chatbot=self.chatbot,
            plan_price=self.price,
            selected_by=self.user,
            provider=PaymentProvider.STRIPE,
            renewal_mode=RenewalMode.PROVIDER_MANAGED,
            status=SubscriptionStatus.INCOMPLETE,
            created_by=self.user,
            updated_by=self.user,
        )

    def test_apply_unknown_code_returns_404(self):
        response = self.client.post(
            self.apply_url,
            {"code": "NOPE", "plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_apply_inactive_coupon_is_rejected(self):
        self.percent_coupon.is_active = False
        self.percent_coupon.save(update_fields=["is_active", "updated_at"])

        response = self.client.post(
            self.apply_url,
            {"code": "save10", "plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_expired_coupon_is_rejected(self):
        self.percent_coupon.valid_until = timezone.now() - timezone.timedelta(
            days=1
        )
        self.percent_coupon.save(update_fields=["valid_until", "updated_at"])

        response = self.client.post(
            self.apply_url,
            {"code": "SAVE10", "plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_returns_percentage_preview_without_subscription(self):
        response = self.client.post(
            self.apply_url,
            {"code": "save10", "plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertFalse(data["bound"])
        self.assertEqual(data["original_amount"], "19.00")
        self.assertEqual(data["discount_amount"], "1.90")
        self.assertEqual(data["final_amount"], "17.10")

    def test_apply_returns_fixed_preview(self):
        response = self.client.post(
            self.apply_url,
            {"code": "FIVEUSD", "plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["discount_amount"], "5.00")
        self.assertEqual(data["final_amount"], "14.00")

    def test_apply_requires_plan_price_without_subscription(self):
        response = self.client.post(
            self.apply_url,
            {"code": "SAVE10"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_rejects_fixed_coupon_for_other_currency(self):
        price = PlanPrice.objects.create(
            plan=self.plan,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.ANNUAL,
            currency="EUR",
            amount=Decimal("199.00"),
        )

        response = self.client.post(
            self.apply_url,
            {"code": "FIVEUSD", "plan_price_id": str(price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_rejects_unmet_minimum_purchase(self):
        self.percent_coupon.minimum_purchase_amount = Decimal("50.00")
        self.percent_coupon.minimum_purchase_currency = "USD"
        self.percent_coupon.save(
            update_fields=[
                "minimum_purchase_amount",
                "minimum_purchase_currency",
                "updated_at",
            ]
        )

        response = self.client.post(
            self.apply_url,
            {"code": "SAVE10", "plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_rejects_ineligible_plan(self):
        other_plan = SubscriptionPlan.objects.create(
            name="Scale",
            ai_message_limit=5000,
        )
        self.percent_coupon.applies_to_all_plans = False
        self.percent_coupon.save(
            update_fields=["applies_to_all_plans", "updated_at"]
        )
        self.percent_coupon.eligible_plans.add(other_plan)

        response = self.client.post(
            self.apply_url,
            {"code": "SAVE10", "plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_rejects_when_per_user_limit_reached(self):
        CouponRedemption.objects.create(
            coupon=self.percent_coupon,
            user=self.user,
            original_amount=Decimal("19.00"),
            discount_amount=Decimal("1.90"),
            final_amount=Decimal("17.10"),
            currency="USD",
        )
        self.percent_coupon.max_redemptions_per_user = 1
        self.percent_coupon.save(
            update_fields=["max_redemptions_per_user", "updated_at"]
        )

        response = self.client.post(
            self.apply_url,
            {"code": "SAVE10", "plan_price_id": str(self.price.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_binds_coupon_to_open_checkout_subscription(self):
        self.create_incomplete_subscription()

        response = self.client.post(
            self.apply_url,
            {"code": "SAVE10"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["data"]["bound"])
        subscription = ChatbotSubscription.objects.get(
            chatbot=self.chatbot,
            status=SubscriptionStatus.INCOMPLETE,
        )
        pending = subscription.provider_metadata["pending_coupon"]
        self.assertEqual(pending["code"], "SAVE10")
        self.assertEqual(pending["id"], str(self.percent_coupon.id))

    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".get_or_create_promotion_code"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".create_checkout_session"
    )
    def test_checkout_with_coupon_binds_and_uses_promotion_code(
        self, create_checkout, get_promotion_code
    ):
        create_checkout.return_value = {
            "id": "cs_test_123",
            "client_secret": "cs_test_123_secret_example",
            "status": "open",
        }
        get_promotion_code.return_value = "promo_123"

        response = self.client.post(
            self.checkout_url,
            {
                "plan_price_id": str(self.price.id),
                "coupon_code": "save10",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        subscription = ChatbotSubscription.objects.get(
            chatbot=self.chatbot,
            status=SubscriptionStatus.INCOMPLETE,
        )
        self.assertEqual(
            subscription.provider_metadata["pending_coupon"]["code"],
            "SAVE10",
        )
        self.assertEqual(
            subscription.provider_metadata["checkout_coupon_id"],
            str(self.percent_coupon.id),
        )
        self.assertEqual(
            response.data["data"]["coupon"]["code"],
            "SAVE10",
        )
        get_promotion_code.assert_called_once()
        _, kwargs = create_checkout.call_args
        self.assertEqual(kwargs["promotion_code_id"], "promo_123")

    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".get_or_create_promotion_code"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".retrieve_checkout_session"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".create_checkout_session"
    )
    def test_checkout_reuses_session_for_the_same_coupon(
        self, create_checkout, retrieve_checkout, get_promotion_code
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
        get_promotion_code.return_value = "promo_123"
        body = {
            "plan_price_id": str(self.price.id),
            "coupon_code": "SAVE10",
        }

        first = self.client.post(self.checkout_url, body, format="json")
        second = self.client.post(self.checkout_url, body, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data["data"]["reused"])
        create_checkout.assert_called_once()

    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".get_or_create_promotion_code"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".expire_checkout_session"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".retrieve_checkout_session"
    )
    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".create_checkout_session"
    )
    def test_checkout_replaces_session_when_coupon_changes(
        self,
        create_checkout,
        retrieve_checkout,
        expire_checkout,
        get_promotion_code,
    ):
        create_checkout.side_effect = [
            {
                "id": "cs_test_1",
                "client_secret": "cs_test_1_secret_example",
                "status": "open",
            },
            {
                "id": "cs_test_2",
                "client_secret": "cs_test_2_secret_example",
                "status": "open",
            },
        ]
        retrieve_checkout.return_value = {
            "id": "cs_test_1",
            "status": "open",
        }
        expire_checkout.return_value = {"id": "cs_test_1", "status": "expired"}
        get_promotion_code.return_value = "promo_changed"

        first = self.client.post(
            self.checkout_url,
            {"plan_price_id": str(self.price.id), "coupon_code": "SAVE10"},
            format="json",
        )
        second = self.client.post(
            self.checkout_url,
            {"plan_price_id": str(self.price.id), "coupon_code": "FIVEUSD"},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertFalse(second.data["data"]["reused"])
        self.assertEqual(create_checkout.call_count, 2)
        expire_checkout.assert_called_once_with(session_id="cs_test_1")
        subscription = ChatbotSubscription.objects.get(
            chatbot=self.chatbot,
            status=SubscriptionStatus.INCOMPLETE,
        )
        self.assertEqual(
            subscription.provider_metadata["pending_coupon"]["code"],
            "FIVEUSD",
        )

    @patch(
        "subscription.services.subscriptions.StripeBillingService"
        ".create_checkout_session"
    )
    def test_checkout_rejects_invalid_coupon_without_side_effects(
        self, create_checkout
    ):
        self.percent_coupon.is_active = False
        self.percent_coupon.save(update_fields=["is_active", "updated_at"])

        response = self.client.post(
            self.checkout_url,
            {"plan_price_id": str(self.price.id), "coupon_code": "SAVE10"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        create_checkout.assert_not_called()
        self.assertFalse(
            ChatbotSubscription.objects.filter(chatbot=self.chatbot).exists()
        )

    def test_current_shows_pending_and_active_redemption(self):
        subscription = self.create_incomplete_subscription()
        redemption = CouponRedemption.objects.create(
            coupon=self.fixed_coupon,
            subscription=subscription,
            user=self.user,
            original_amount=Decimal("19.00"),
            discount_amount=Decimal("5.00"),
            final_amount=Decimal("14.00"),
            currency="USD",
        )
        subscription.provider_metadata = {
            "pending_coupon": {
                "id": str(self.percent_coupon.id),
                "code": "SAVE10",
                "name": "",
            }
        }
        subscription.save(update_fields=["provider_metadata", "updated_at"])

        response = self.client.get(self.current_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["pending_coupon"]["coupon"]["code"], "SAVE10")
        self.assertEqual(
            data["coupon_redemption"]["coupon_code_snapshot"],
            "FIVEUSD",
        )
        self.assertEqual(
            data["coupon_redemption"]["id"], str(redemption.id)
        )

    def test_remove_clears_pending_coupon(self):
        self.create_incomplete_subscription()
        self.client.post(self.apply_url, {"code": "SAVE10"}, format="json")

        response = self.client.post(self.remove_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["data"]["removed"])
        subscription = ChatbotSubscription.objects.get(
            chatbot=self.chatbot,
            status=SubscriptionStatus.INCOMPLETE,
        )
        self.assertNotIn(
            "pending_coupon", subscription.provider_metadata or {}
        )

    def test_current_subscription_serializer_includes_coupon_fields(self):
        self.create_incomplete_subscription()

        response = self.client.get(
            f'{reverse("current-subscription")}?chatbot={self.chatbot.slug}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription_data = response.data["data"]["subscription"]
        self.assertIsNone(subscription_data["pending_coupon"])
        self.assertIsNone(subscription_data["coupon_redemption"])
