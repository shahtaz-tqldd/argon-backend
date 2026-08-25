from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from subscription.choices import BillingInterval, PaymentProvider
from subscription.models import Payment, PlanPrice, SubscriptionPlan


class SubscriptionPlanAdminAPITests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            email="admin@example.com",
            password="strong-password",
        )
        self.client.force_authenticate(self.admin)
        self.payload = {
            "name": "Growth",
            "plan_type": "standard",
            "ai_message_limit": 1000,
            "file_size_limit_mb": 25,
            "knowledge_chunk_limit": 5000,
            "ai_message_overage_enabled": True,
            "features": ["knowledge_base", "lead_capture"],
            "details_html": "<p>For growing teams</p>",
            "is_free": False,
            "is_public": True,
            "requires_sales_contact": False,
            "is_active": True,
            "sort_order": 10,
        }

    def create_plan(self, **overrides):
        values = {**self.payload, **overrides}
        return SubscriptionPlan.objects.create(
            **values,
            created_by=self.admin,
            updated_by=self.admin,
        )

    def test_superadmin_can_create_plan(self):
        response = self.client.post(
            reverse("subscription-plan-create"),
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["slug"], "growth")
        plan = SubscriptionPlan.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(plan.created_by, self.admin)
        self.assertEqual(plan.updated_by, self.admin)

    def test_superadmin_can_partially_update_plan_without_changing_slug(self):
        plan = self.create_plan()

        response = self.client.patch(
            reverse("subscription-plan-update", kwargs={"plan_id": plan.id}),
            {"name": "Growth Plus", "sort_order": 20},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        plan.refresh_from_db()
        self.assertEqual(plan.name, "Growth Plus")
        self.assertEqual(plan.slug, "growth")
        self.assertEqual(plan.updated_by, self.admin)

    def test_superadmin_can_delete_unused_plan(self):
        plan = self.create_plan()

        response = self.client.delete(
            reverse("subscription-plan-delete", kwargs={"plan_id": plan.id}),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(SubscriptionPlan.objects.filter(pk=plan.id).exists())

    def test_plan_with_payment_history_cannot_be_deleted(self):
        plan = self.create_plan()
        price = PlanPrice.objects.create(
            plan=plan,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("19.00"),
            ai_message_overage_unit_price=Decimal("0.01"),
        )
        Payment.objects.create(
            plan_price=price,
            user=self.admin,
            provider=PaymentProvider.STRIPE,
            amount=Decimal("19.00"),
            currency="USD",
        )

        response = self.client.delete(
            reverse("subscription-plan-delete", kwargs={"plan_id": plan.id}),
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(SubscriptionPlan.objects.filter(pk=plan.id).exists())

    def test_overage_requires_a_message_limit(self):
        response = self.client.post(
            reverse("subscription-plan-create"),
            {
                **self.payload,
                "ai_message_limit": None,
                "ai_message_overage_enabled": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ai_message_limit", response.data)

    def test_duplicate_features_are_rejected(self):
        response = self.client.post(
            reverse("subscription-plan-create"),
            {
                **self.payload,
                "features": ["knowledge_base", "knowledge_base"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("features", response.data)

    def test_non_superadmin_cannot_manage_plans(self):
        user = get_user_model().objects.create_user(
            email="member@example.com",
            password="strong-password",
        )
        self.client.force_authenticate(user)

        response = self.client.post(
            reverse("subscription-plan-create"),
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
