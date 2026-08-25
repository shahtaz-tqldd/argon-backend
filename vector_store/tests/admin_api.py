from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from knowledge.models import KnowledgeBase
from vector_store.api.v1.admin.serializers import (
    VectorRecordListQuerySerializer,
    VectorRecordSerializer,
)
from vector_store.api.v1.admin.views import (
    VectorRecordListAPIView,
    VectorSemanticSearchAPIView,
)
from vector_store.models import VectorDocument
from vector_store.services.vectorize import VectorSearchResult


class FakeVectorQuerySet:
    def __init__(self, records):
        self.records = records
        self.filters = []

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return self

    def count(self):
        return len(self.records)

    def __getitem__(self, item):
        return self.records[item]


class VectorStoreAdminAPITests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = SimpleNamespace(is_authenticated=True, is_superuser=True)

    def _authenticate(self, request):
        force_authenticate(request, user=self.admin)
        return request

    def _record(self):
        now = timezone.now()
        knowledge_base = KnowledgeBase(
            id=uuid4(),
            chatbot_id=uuid4(),
        )
        return VectorDocument(
            id=uuid4(),
            knowledge_base=knowledge_base,
            chunk_index=0,
            token_count=12,
            content_hash="a" * 64,
            content="Knowledge vector record.",
            metadata={"title": "Record"},
            embedding=[0.1] * 1536,
            created_at=now,
            updated_at=now,
        )

    def test_list_filters_accept_only_knowledge_identifiers(self):
        chatbot_id = uuid4()
        knowledge_base_id = uuid4()
        serializer = VectorRecordListQuerySerializer(
            data={
                "chatbot_id": str(chatbot_id),
                "knowledge_base_id": str(knowledge_base_id),
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["chatbot_id"], chatbot_id)
        self.assertEqual(
            serializer.validated_data["knowledge_base_id"],
            knowledge_base_id,
        )

    def test_record_serializer_never_exposes_embedding_or_source_type(self):
        data = VectorRecordSerializer(self._record()).data
        self.assertNotIn("embedding", data)
        self.assertNotIn("source_type", data)
        self.assertNotIn("source_id", data)
        self.assertIn("knowledge_base_id", data)

    @patch.object(VectorRecordListAPIView, "get_queryset")
    def test_list_filters_by_chatbot_and_knowledge_base(self, get_queryset):
        chatbot_id = uuid4()
        knowledge_base_id = uuid4()
        queryset = FakeVectorQuerySet([self._record(), self._record()])
        get_queryset.return_value = queryset
        request = self._authenticate(
            self.factory.get(
                "/api/v1/admin/vector-store/records/",
                {
                    "chatbot_id": str(chatbot_id),
                    "knowledge_base_id": str(knowledge_base_id),
                    "page_size": 1,
                },
            )
        )

        response = VectorRecordListAPIView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            queryset.filters,
            [
                {"knowledge_base__chatbot_id": chatbot_id},
                {"knowledge_base_id": knowledge_base_id},
            ],
        )

    @patch("vector_store.api.v1.admin.views.KnowledgeVectorService.search")
    def test_semantic_search_uses_knowledge_filters(self, search):
        chatbot_id = uuid4()
        knowledge_base_id = uuid4()
        search.return_value = [
            VectorSearchResult(
                id=str(uuid4()),
                knowledge_base_id=str(knowledge_base_id),
                chatbot_id=str(chatbot_id),
                chunk_index=0,
                token_count=20,
                content="Relevant knowledge.",
                metadata={"title": "Policy"},
                distance=0.12,
            )
        ]
        request = self._authenticate(
            self.factory.post(
                "/api/v1/admin/vector-store/search/",
                {
                    "query": "refund policy",
                    "chatbot_id": str(chatbot_id),
                    "knowledge_base_ids": [str(knowledge_base_id)],
                    "limit": 5,
                },
                format="json",
            )
        )

        response = VectorSemanticSearchAPIView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        search.assert_called_once_with(
            "refund policy",
            limit=5,
            chatbot_id=chatbot_id,
            knowledge_base_ids=[knowledge_base_id],
        )
        self.assertEqual(
            response.data["data"][0]["knowledge_base_id"],
            str(knowledge_base_id),
        )
