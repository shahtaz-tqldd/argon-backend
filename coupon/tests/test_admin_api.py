from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from coupon.models import Coupon, CouponRedemption, Discount
from subscription.models import SubscriptionPlan


class CouponAdminAPITests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            email="admin@example.com",
            password="strong-password",
        )
        self.client.force_authenticate(self.admin)
        self.plan = SubscriptionPlan.objects.create(
            name="Growth",
            ai_message_limit=1000,
        )
        self.percentage_discount = {
            "name": "Launch 10%",
            "discount_type": "percentage",
            "value": "10.00",
            "currency": "",
            "duration": "once",
            "duration_in_billing_cycles": None,
        }
        self.payload = {
            "code": "save10",
            "name": "Launch offer",
            "discount_data": dict(self.percentage_discount),
            "is_active": True,
            "max_redemptions": 100,
            "max_redemptions_per_user": 1,
            "first_payment_only": False,
            "applies_to_all_plans": True,
            "metadata": {},
        }

    def create_coupon(self, **overrides):
        values = {**self.payload, **overrides}
        discount_data = values.pop("discount_data")
        discount = Discount.objects.create(**discount_data)
        return Coupon.objects.create(
            discount=discount,
            created_by=self.admin,
            updated_by=self.admin,
            **values,
        )

    def create_redemption(self, coupon, **overrides):
        original = Decimal(overrides.pop("original_amount", "19.00"))
        discount_amount = Decimal(
            overrides.pop("discount_amount", "1.90")
        )
        return CouponRedemption.objects.create(
            coupon=coupon,
            original_amount=original,
            discount_amount=discount_amount,
            final_amount=original - discount_amount,
            currency=overrides.pop("currency", "USD"),
            **overrides,
        )

    def test_superadmin_can_create_coupon_with_nested_discount(self):
        response = self.client.post(
            reverse("coupon-create"),
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        coupon = Coupon.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(coupon.code, "SAVE10")
        self.assertEqual(coupon.discount.name, "Launch 10%")
        self.assertEqual(coupon.created_by, self.admin)
        self.assertEqual(coupon.discount.created_by, self.admin)

    def test_superadmin_can_create_coupon_with_existing_discount_id(self):
        discount = Discount.objects.create(
            name="Partner deal",
            discount_type="fixed_amount",
            value=Decimal("5.00"),
            currency="USD",
        )
        payload = {k: v for k, v in self.payload.items() if k != "discount_data"}

        response = self.client.post(
            reverse("coupon-create"),
            {**payload, "discount_id": str(discount.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        coupon = Coupon.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(coupon.discount, discount)
        self.assertEqual(Discount.objects.count(), 1)

    def test_create_requires_a_discount(self):
        payload = {k: v for k, v in self.payload.items() if k != "discount_data"}

        response = self.client.post(
            reverse("coupon-create"),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("discount", response.data)

    def test_create_rejects_both_discount_inputs(self):
        discount = Discount.objects.create(
            name="Partner deal",
            discount_type="fixed_amount",
            value=Decimal("5.00"),
            currency="USD",
        )

        response = self.client.post(
            reverse("coupon-create"),
            {
                **self.payload,
                "discount_id": str(discount.id),
                "discount_data": dict(self.percentage_discount),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("discount", response.data)

    def test_percentage_discount_cannot_have_currency(self):
        response = self.client.post(
            reverse("coupon-create"),
            {
                **self.payload,
                "discount_data": {
                    **self.percentage_discount,
                    "currency": "USD",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("currency", response.data["discount_data"])

    def test_fixed_discount_requires_currency(self):
        response = self.client.post(
            reverse("coupon-create"),
            {
                **self.payload,
                "discount_data": {
                    "name": "Five off",
                    "discount_type": "fixed_amount",
                    "value": "5.00",
                    "currency": "",
                    "duration": "once",
                    "duration_in_billing_cycles": None,
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("currency", response.data["discount_data"])

    def test_repeating_discount_requires_cycles(self):
        response = self.client.post(
            reverse("coupon-create"),
            {
                **self.payload,
                "discount_data": {
                    **self.percentage_discount,
                    "duration": "repeating",
                    "duration_in_billing_cycles": None,
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "duration_in_billing_cycles",
            response.data["discount_data"],
        )

    def test_valid_until_must_follow_valid_from(self):
        response = self.client.post(
            reverse("coupon-create"),
            {
                **self.payload,
                "valid_from": "2026-09-01T00:00:00Z",
                "valid_until": "2026-08-01T00:00:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("valid_until", response.data)

    def test_eligible_plans_required_when_not_all_plans(self):
        response = self.client.post(
            reverse("coupon-create"),
            {**self.payload, "applies_to_all_plans": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("eligible_plans", response.data)

    def test_duplicate_code_is_rejected(self):
        self.create_coupon()

        response = self.client.post(
            reverse("coupon-create"),
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("code", response.data)

    def test_superadmin_can_partially_update_coupon(self):
        coupon = self.create_coupon()

        response = self.client.patch(
            reverse("coupon-update", kwargs={"coupon_id": coupon.id}),
            {"is_active": False, "max_redemptions": 10},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        coupon.refresh_from_db()
        self.assertFalse(coupon.is_active)
        self.assertEqual(coupon.max_redemptions, 10)
        self.assertEqual(coupon.updated_by, self.admin)

    def test_discount_swap_rejected_when_redemptions_exist(self):
        coupon = self.create_coupon()
        self.create_redemption(coupon)
        other_discount = Discount.objects.create(
            name="Other",
            discount_type="fixed_amount",
            value=Decimal("5.00"),
            currency="USD",
        )

        response = self.client.patch(
            reverse("coupon-update", kwargs={"coupon_id": coupon.id}),
            {"discount_id": str(other_discount.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("discount_id", response.data)

    def test_superadmin_can_delete_unused_coupon(self):
        coupon = self.create_coupon()

        response = self.client.delete(
            reverse("coupon-delete", kwargs={"coupon_id": coupon.id}),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Coupon.objects.filter(pk=coupon.id).exists())
        # The discount definition survives; deleting a coupon never cascade
        # deletes the shared discount behind it.
        self.assertTrue(Discount.objects.exists())

    def test_coupon_with_redemptions_cannot_be_deleted(self):
        coupon = self.create_coupon()
        self.create_redemption(coupon)

        response = self.client.delete(
            reverse("coupon-delete", kwargs={"coupon_id": coupon.id}),
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Coupon.objects.filter(pk=coupon.id).exists())

    def test_usage_endpoint_aggregates_redemptions(self):
        coupon = self.create_coupon()
        member = get_user_model().objects.create_user(
            email="member@example.com",
            password="strong-password",
        )
        self.create_redemption(coupon, user=member)
        self.create_redemption(coupon, user=self.admin)
        self.create_redemption(
            coupon,
            user=member,
            original_amount="25.00",
            discount_amount="2.50",
            currency="EUR",
        )

        response = self.client.get(
            reverse("coupon-usage", kwargs={"coupon_id": coupon.id}),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        usage = response.data["data"]["usage"]
        self.assertEqual(usage["total_redemptions"], 3)
        self.assertEqual(usage["active_redemptions"], 3)
        self.assertEqual(usage["unique_users"], 2)
        self.assertEqual(usage["total_original_amount"], "63.00")
        self.assertEqual(usage["total_discount_amount"], "6.30")
        self.assertEqual(usage["total_final_amount"], "56.70")
        per_currency = {
            row["currency"]: row for row in usage["per_currency"]
        }
        self.assertEqual(per_currency["USD"]["redemptions"], 2)
        self.assertEqual(per_currency["USD"]["discount_amount"], "3.80")
        self.assertEqual(per_currency["EUR"]["redemptions"], 1)
        self.assertEqual(
            per_currency["EUR"]["discount_amount"],
            "2.50",
        )

    def test_redemptions_list_filters_by_active_state(self):
        coupon = self.create_coupon()
        active = self.create_redemption(coupon)
        inactive = self.create_redemption(coupon)
        inactive.is_active = False
        inactive.save(update_fields=["is_active", "updated_at"])

        response = self.client.get(
            reverse("coupon-redemptions", kwargs={"coupon_id": coupon.id}),
            {"is_active": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(active.id))
        self.assertTrue(rows[0]["is_active"])

    def test_non_superadmin_cannot_manage_coupons(self):
        user = get_user_model().objects.create_user(
            email="member@example.com",
            password="strong-password",
        )
        self.client.force_authenticate(user)

        response = self.client.post(
            reverse("coupon-create"),
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
