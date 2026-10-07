from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from lead_capture.models import LeadCaptureConfig
from subscription.choices import (
    BillingInterval,
    EnterprisePlanRequestStatus,
    PaymentProvider,
    PlanFeature,
    PlanType,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import (
    ChatbotSubscription,
    EnterprisePlanRequest,
    PlanPrice,
    SubscriptionPlan,
)
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser


class EnterprisePlanRequestBase(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="owner@example.com",
            password="strong-password",
        )
        self.superadmin = get_user_model().objects.create_superuser(
            email="superadmin@example.com",
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

    def submit_request(self, **overrides):
        payload = {
            "requested_features": [
                PlanFeature.KNOWLEDGE_BASE,
                PlanFeature.LEAD_CAPTURE,
            ],
            "requested_ai_message_limit": 50000,
            "requested_file_size_limit_mb": 200,
            "requested_knowledge_chunk_limit": 10000,
            "requested_daily_traffic": 25000,
            "notes": "We need extra capacity for the holiday season.",
        }
        payload.update(overrides)
        return self.client.post(
            f'{reverse("enterprise-plan-request-create")}'
            f"?chatbot={self.chatbot.slug}",
            payload,
            format="json",
        )


class EnterprisePlanRequestCreateTests(EnterprisePlanRequestBase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_chatbot_admin_can_submit_request(self):
        response = self.submit_request()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(EnterprisePlanRequest.objects.count(), 1)
        plan_request = EnterprisePlanRequest.objects.get(
            chatbot=self.chatbot
        )
        self.assertEqual(
            plan_request.status,
            EnterprisePlanRequestStatus.PENDING,
        )
        self.assertEqual(
            plan_request.requested_features,
            [PlanFeature.KNOWLEDGE_BASE, PlanFeature.LEAD_CAPTURE],
        )
        self.assertEqual(plan_request.requested_ai_message_limit, 50000)
        self.assertEqual(plan_request.requested_file_size_limit_mb, 200)
        self.assertEqual(
            plan_request.requested_knowledge_chunk_limit,
            10000,
        )
        self.assertEqual(plan_request.requested_daily_traffic, 25000)
        self.assertEqual(
            plan_request.notes,
            "We need extra capacity for the holiday season.",
        )
        self.assertEqual(
            response.data["data"]["chatbot_slug"],
            self.chatbot.slug,
        )
        self.assertEqual(response.data["data"]["status"], "pending")

    def test_duplicate_pending_request_is_conflicting(self):
        self.submit_request()

        response = self.submit_request()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(EnterprisePlanRequest.objects.count(), 1)

    def test_non_member_cannot_submit_request(self):
        outsider = get_user_model().objects.create_user(
            email="outsider@example.com",
            password="strong-password",
        )
        self.client.force_authenticate(outsider)

        response = self.submit_request()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(EnterprisePlanRequest.objects.count(), 0)

    def test_unknown_feature_is_rejected(self):
        response = self.submit_request(requested_features=["telepathy"])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(EnterprisePlanRequest.objects.count(), 0)


class EnterprisePlanRequestListTests(EnterprisePlanRequestBase):
    def setUp(self):
        super().setUp()
        self.plan_request = EnterprisePlanRequest.objects.create(
            chatbot=self.chatbot,
            requested_daily_traffic=25000,
            created_by=self.user,
        )

    def test_superadmin_can_list_requests(self):
        self.client.force_authenticate(self.superadmin)

        response = self.client.get(reverse("enterprise-plan-request-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["meta"]["count"],
            1,
        )
        row = response.data["data"][0]
        self.assertEqual(row["id"], str(self.plan_request.id))
        self.assertEqual(row["chatbot_slug"], self.chatbot.slug)
        self.assertEqual(row["workspace_name"], self.workspace.name)

    def test_list_filters_by_status_and_chatbot(self):
        approved = EnterprisePlanRequest.objects.create(
            chatbot=self.chatbot,
            status=EnterprisePlanRequestStatus.APPROVED,
            created_by=self.user,
        )
        self.client.force_authenticate(self.superadmin)

        pending = self.client.get(
            reverse("enterprise-plan-request-list"),
            {"status": "pending"},
        )
        approved_response = self.client.get(
            reverse("enterprise-plan-request-list"),
            {"status": "approved", "chatbot": self.chatbot.slug},
        )

        self.assertEqual(pending.status_code, status.HTTP_200_OK)
        self.assertEqual(pending.data["meta"]["count"], 1)
        self.assertEqual(pending.data["data"][0]["status"], "pending")
        self.assertEqual(approved_response.status_code, status.HTTP_200_OK)
        self.assertEqual(approved_response.data["meta"]["count"], 1)
        self.assertEqual(
            approved_response.data["data"][0]["id"],
            str(approved.id),
        )

    def test_non_superadmin_cannot_list_requests(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(reverse("enterprise-plan-request-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class EnterprisePlanRequestApproveTests(EnterprisePlanRequestBase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.superadmin)
        self.plan_request = EnterprisePlanRequest.objects.create(
            chatbot=self.chatbot,
            requested_features=[PlanFeature.LEAD_CAPTURE],
            requested_ai_message_limit=50000,
            created_by=self.user,
        )
        self.expires_at = timezone.now() + timedelta(days=365)

    def approve(self, **overrides):
        payload = {
            "features": [
                PlanFeature.KNOWLEDGE_BASE,
                PlanFeature.LEAD_CAPTURE,
            ],
            "ai_message_limit": 60000,
            "file_size_limit_mb": 250,
            "knowledge_chunk_limit": 12000,
            "expires_at": self.expires_at.isoformat(),
            "review_notes": "Approved for one year.",
        }
        payload.update(overrides)
        return self.client.post(
            reverse(
                "enterprise-plan-request-approve",
                kwargs={"request_id": str(self.plan_request.id)},
            ),
            payload,
            format="json",
        )

    def test_approval_creates_custom_subscription_contract(self):
        response = self.approve()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.plan_request.refresh_from_db()
        self.assertEqual(
            self.plan_request.status,
            EnterprisePlanRequestStatus.APPROVED,
        )
        self.assertEqual(
            self.plan_request.approved_features,
            [PlanFeature.KNOWLEDGE_BASE, PlanFeature.LEAD_CAPTURE],
        )
        self.assertEqual(
            self.plan_request.approved_ai_message_limit,
            60000,
        )
        self.assertEqual(
            self.plan_request.approved_file_size_limit_mb,
            250,
        )
        self.assertEqual(
            self.plan_request.approved_knowledge_chunk_limit,
            12000,
        )
        self.assertEqual(self.plan_request.reviewed_by, self.superadmin)
        self.assertIsNotNone(self.plan_request.reviewed_at)

        subscription = self.plan_request.subscription
        self.assertIsNotNone(subscription)
        self.assertEqual(subscription.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(subscription.provider, PaymentProvider.MANUAL)
        self.assertEqual(subscription.renewal_mode, RenewalMode.MANUAL)
        self.assertEqual(subscription.current_period_end, self.expires_at)
        self.assertEqual(subscription.next_billing_at, self.expires_at)

        plan = subscription.plan_price.plan
        self.assertEqual(plan.plan_type, PlanType.CUSTOM)
        self.assertFalse(plan.is_public)
        self.assertEqual(plan.ai_message_limit, 60000)
        self.assertEqual(plan.features, list(plan.features))
        self.assertEqual(subscription.plan_price.amount, Decimal("0.00"))
        self.assertEqual(
            subscription.plan_price.billing_interval,
            BillingInterval.CUSTOM,
        )

        self.assertEqual(
            subscription.snapshot["limits"]["ai_message_limit"],
            60000,
        )
        self.assertEqual(
            subscription.snapshot["plan"]["features"],
            [PlanFeature.KNOWLEDGE_BASE, PlanFeature.LEAD_CAPTURE],
        )
        self.assertTrue(
            LeadCaptureConfig.objects.filter(
                chatbot=self.chatbot
            ).exists(),
        )
        self.assertEqual(
            response.data["data"]["subscription"]["ai_message_limit"],
            60000,
        )

    def test_approval_cancels_existing_open_subscription(self):
        existing_plan = SubscriptionPlan.objects.create(
            name="Growth",
            ai_message_limit=2500,
        )
        existing_price = PlanPrice.objects.create(
            plan=existing_plan,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("119.00"),
        )
        existing_subscription = ChatbotSubscription.objects.create(
            chatbot=self.chatbot,
            plan_price=existing_price,
            selected_by=self.user,
            provider=PaymentProvider.STRIPE,
            renewal_mode=RenewalMode.PROVIDER_MANAGED,
            status=SubscriptionStatus.ACTIVE,
        )

        response = self.approve()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        existing_subscription.refresh_from_db()
        self.assertEqual(
            existing_subscription.status,
            SubscriptionStatus.CANCELED,
        )
        self.assertEqual(
            ChatbotSubscription.objects.filter(
                chatbot=self.chatbot,
                status=SubscriptionStatus.ACTIVE,
            ).count(),
            1,
        )

    def test_approving_twice_is_conflicting(self):
        self.approve()

        response = self.approve()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            ChatbotSubscription.objects.filter(
                chatbot=self.chatbot
            ).count(),
            1,
        )

    def test_non_superadmin_cannot_approve(self):
        self.client.force_authenticate(self.user)

        response = self.approve()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.plan_request.refresh_from_db()
        self.assertEqual(
            self.plan_request.status,
            EnterprisePlanRequestStatus.PENDING,
        )

    def test_past_expiry_date_is_rejected(self):
        response = self.approve(
            expires_at=(timezone.now() - timedelta(days=1)).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.plan_request.refresh_from_db()
        self.assertEqual(
            self.plan_request.status,
            EnterprisePlanRequestStatus.PENDING,
        )
