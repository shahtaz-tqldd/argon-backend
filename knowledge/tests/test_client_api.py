from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.services import create_chatbot
from knowledge.models import KnowledgeBase, KnowledgeTrainingLog
from knowledge.utils.choices import (
    KnowledgeSourceTypes,
    KnowledgeTrainingStageTypes,
    StatusTypes,
)
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
            name="Knowledge Bot",
            created_by=self.owner,
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
