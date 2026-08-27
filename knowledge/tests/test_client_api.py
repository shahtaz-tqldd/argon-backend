from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.services import create_chatbot
from knowledge.models import KnowledgeBase, KnowledgeTrainingLog
from knowledge.services import (
    KnowledgeLimitExceeded,
    validate_knowledge_chunk_capacity,
)
from knowledge.utils.choices import (
    KnowledgeSourceTypes,
    KnowledgeTrainingStageTypes,
    StatusTypes,
)
from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    PlanFeature,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription, PlanPrice, SubscriptionPlan
from vector_store.models import VectorDocument
from workspace.services import ensure_personal_workspace


User = get_user_model()


class KnowledgeClientAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="knowledge-owner@example.com",
            password="StrongPass123!",
        )
        self.workspace = ensure_personal_workspace(self.owner)
        self.chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Knowledge Bot",
            created_by=self.owner,
        )
        self.plan = SubscriptionPlan.objects.create(
            name="Free",
            ai_message_limit=100,
            file_size_limit_mb=10,
            knowledge_chunk_limit=2,
            features=[PlanFeature.KNOWLEDGE_BASE],
            is_free=True,
        )
        self.price = PlanPrice.objects.create(
            plan=self.plan,
            provider=PaymentProvider.MANUAL,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("0.00"),
        )
        self.subscription = ChatbotSubscription.objects.create(
            chatbot=self.chatbot,
            plan_price=self.price,
            selected_by=self.owner,
            provider=PaymentProvider.MANUAL,
            renewal_mode=RenewalMode.MANUAL,
            status=SubscriptionStatus.ACTIVE,
        )
        self.client.force_authenticate(self.owner)

    def create_knowledge(self, *, source_type=KnowledgeSourceTypes.TEXT, **values):
        defaults = {
            "chatbot": self.chatbot,
            "source_type": source_type,
            "title": "Source",
            "created_by": self.owner,
            "updated_by": self.owner,
        }
        if source_type == KnowledgeSourceTypes.TEXT:
            defaults["text_content"] = "Original custom knowledge."
        elif source_type == KnowledgeSourceTypes.WEBSITE:
            defaults["url"] = "https://example.com/knowledge"
        else:
            defaults.update(
                original_filename="source.txt",
                file_type="txt",
                file_key="knowledge/source.txt",
                file_size=10,
            )
        defaults.update(values)
        return KnowledgeBase.objects.create(**defaults)

    @patch("knowledge.tasks.train_knowledge_base.delay")
    def test_upload_uses_chatbot_and_type_query_parameters(self, delay):
        delay.return_value = SimpleNamespace(id="task-upload")

        response = self.client.post(
            reverse("knowledge-upload"),
            {"content": "Custom support documentation.", "title": "Support"},
            format="json",
            query_params={"chatbot": self.chatbot.slug, "type": "custom"},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        source = KnowledgeBase.objects.get()
        self.assertEqual(source.source_type, KnowledgeSourceTypes.TEXT)
        self.assertEqual(source.text_content, "Custom support documentation.")
        self.assertEqual(
            response.data["data"]["training"]["knowledge_base_id"],
            str(source.id),
        )

    def test_list_is_paginated_and_scoped_by_chatbot(self):
        self.create_knowledge(title="First")
        self.create_knowledge(title="Second")

        response = self.client.get(
            reverse("knowledge-list"),
            {"chatbot": self.chatbot.slug, "page": 1, "page_size": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["num_pages"], 2)
        self.assertEqual(
            set(response.data["data"][0]),
            {
                "id",
                "name",
                "url",
                "source_type",
                "file_type",
                "file_size",
                "is_enabled",
                "status",
                "last_crawled_at",
                "processed_at",
                "created_at",
                "updated_at",
            },
        )

    def test_usage_returns_chatbot_totals_and_subscription_snapshot_limits(self):
        first_file = self.create_knowledge(
            source_type=KnowledgeSourceTypes.FILE,
            file_size=10,
        )
        second_file = self.create_knowledge(
            source_type=KnowledgeSourceTypes.FILE,
            file_size=15,
        )
        other_chatbot = create_chatbot(
            workspace=self.workspace,
            chatbot_name="Other Knowledge Bot",
            created_by=self.owner,
        )
        other_file = KnowledgeBase.objects.create(
            chatbot=other_chatbot,
            source_type=KnowledgeSourceTypes.FILE,
            title="Other source",
            original_filename="other.txt",
            file_type="txt",
            file_key="knowledge/other.txt",
            file_size=100,
            created_by=self.owner,
            updated_by=self.owner,
        )
        for knowledge_base, chunk_count in ((first_file, 2), (other_file, 1)):
            for chunk_index in range(chunk_count):
                VectorDocument.objects.create(
                    knowledge_base=knowledge_base,
                    chunk_index=chunk_index,
                    token_count=3,
                    content="Knowledge chunk.",
                    metadata={"chunk_count": chunk_count},
                    embedding=[0.1] * 1536,
                )

        self.plan.file_size_limit_mb = 1
        self.plan.knowledge_chunk_limit = 1
        self.plan.save()

        response = self.client.get(
            reverse("knowledge-usage"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"],
            {
                "total_chunks": 2,
                "chunk_limit": 2,
                "total_file_size_bytes": 25,
                "file_size_limit_bytes": 10 * 1024 * 1024,
                "file_size_limit_mb": 10,
            },
        )
        with self.assertRaises(KnowledgeLimitExceeded):
            validate_knowledge_chunk_capacity(second_file, 1)

    @patch("knowledge.tasks.train_knowledge_base.delay")
    def test_upload_requires_an_active_knowledge_subscription(self, delay):
        self.subscription.status = SubscriptionStatus.CANCELED
        self.subscription.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            reverse("knowledge-upload"),
            {"content": "Custom support documentation."},
            format="json",
            query_params={"chatbot": self.chatbot.slug, "type": "custom"},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        delay.assert_not_called()

    def test_details_uses_knowledge_base_id_query_parameter(self):
        source = self.create_knowledge()

        response = self.client.get(
            reverse("knowledge-detail"),
            {"knowledge_base_id": source.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["id"], str(source.id))

    @patch("knowledge.tasks.train_knowledge_base.delay")
    def test_custom_update_replaces_content_and_forces_training(self, delay):
        delay.return_value = SimpleNamespace(id="task-custom")
        source = self.create_knowledge(status=StatusTypes.READY)

        response = self.client.patch(
            reverse("knowledge-update"),
            {"content": "Replacement custom knowledge."},
            format="json",
            query_params={"knowledge_base_id": source.id, "type": "custom"},
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        source.refresh_from_db()
        self.assertEqual(source.text_content, "Replacement custom knowledge.")
        log = source.training_logs.get()
        self.assertTrue(log.force_retrain)

    @patch("knowledge.tasks.train_knowledge_base.delay")
    def test_metadata_update_renames_and_disables_without_retraining(self, delay):
        api_types = {
            KnowledgeSourceTypes.FILE: "file",
            KnowledgeSourceTypes.WEBSITE: "url",
            KnowledgeSourceTypes.TEXT: "custom",
        }

        for source_type, api_type in api_types.items():
            with self.subTest(source_type=source_type):
                source = self.create_knowledge(
                    source_type=source_type,
                    status=StatusTypes.READY,
                )

                response = self.client.patch(
                    reverse("knowledge-update"),
                    {"title": "Renamed source", "is_enabled": False},
                    format="json",
                    query_params={
                        "knowledge_base_id": source.id,
                        "type": api_type,
                    },
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                source.refresh_from_db()
                self.assertEqual(source.title, "Renamed source")
                self.assertFalse(source.is_enabled)
                self.assertFalse(source.training_logs.exists())

        delay.assert_not_called()

    @patch("knowledge.tasks.train_knowledge_base.delay")
    def test_file_update_retries_only_a_failed_training(self, delay):
        delay.return_value = SimpleNamespace(id="task-file")
        failed_source = self.create_knowledge(
            source_type=KnowledgeSourceTypes.FILE,
            status=StatusTypes.FAILED,
        )

        response = self.client.patch(
            reverse("knowledge-update"),
            query_params={"knowledge_base_id": failed_source.id, "type": "file"},
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(failed_source.training_logs.get().force_retrain)

        ready_source = self.create_knowledge(
            source_type=KnowledgeSourceTypes.FILE,
            status=StatusTypes.READY,
        )
        response = self.client.patch(
            reverse("knowledge-update"),
            query_params={"knowledge_base_id": ready_source.id, "type": "file"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("knowledge.tasks.train_knowledge_base.delay")
    def test_url_update_queues_forced_retraining(self, delay):
        delay.return_value = SimpleNamespace(id="task-url")
        source = self.create_knowledge(
            source_type=KnowledgeSourceTypes.WEBSITE,
            status=StatusTypes.READY,
        )

        response = self.client.patch(
            reverse("knowledge-update"),
            query_params={"knowledge_base_id": source.id, "type": "url"},
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(source.training_logs.get().force_retrain)

    def test_training_logs_are_paginated_for_the_chatbot(self):
        first = self.create_knowledge(title="First")
        second = self.create_knowledge(title="Second")
        for source in (first, second):
            KnowledgeTrainingLog.objects.create(
                knowledge_base=source,
                stage=KnowledgeTrainingStageTypes.COMPLETED,
            )

        response = self.client.get(
            reverse("knowledge-training-list"),
            {"chatbot": self.chatbot.slug, "page": 1, "page_size": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertIn("knowledge_base_id", response.data["data"][0])

    def test_delete_uses_knowledge_base_id_query_parameter(self):
        source = self.create_knowledge()

        response = self.client.delete(
            reverse("knowledge-delete"),
            query_params={"knowledge_base_id": source.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(KnowledgeBase.objects.filter(pk=source.id).exists())
