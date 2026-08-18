from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from vector_store.services.vectorize import VectorSearchResult
from vector_store.api.v1.admin.serializers import (
    VectorRecordListQuerySerializer,
    VectorRecordSerializer,
)
from vector_store.api.v1.admin.views import (
    VectorRecordBulkDeleteAPIView,
    VectorRecordDeleteAPIView,
    VectorRecordListAPIView,
    VectorSemanticSearchAPIView,
)
from vector_store.models import VectorDocument


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

    def _record(self, *, source_type=VectorDocument.SourceType.DESTINATION):
        now = timezone.now()
        return VectorDocument(
            id=uuid4(),
            source_type=source_type,
            source_id=uuid4(),
            content="Semantic vector record content.",
            metadata={"destination_id": str(uuid4()), "name": "Record"},
            embedding=[0.1] * 1536,
            created_at=now,
            updated_at=now,
        )

    def test_source_type_filter_accepts_singular_plural_and_comma_values(self):
        serializer = VectorRecordListQuerySerializer(
            data={
                "source_type": "destinations,attractions,cuisine,activities",
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["source_type"],
            [
                VectorDocument.SourceType.DESTINATION,
                VectorDocument.SourceType.ATTRACTION,
                VectorDocument.SourceType.CUISINE,
                VectorDocument.SourceType.ACTIVITY,
            ],
        )

    def test_source_type_filter_rejects_unknown_values(self):
        serializer = VectorRecordListQuerySerializer(
            data={"source_type": "destination,hotel"},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("source_type", serializer.errors)

    def test_record_serializer_never_exposes_embedding(self):
        data = VectorRecordSerializer(self._record()).data

        self.assertNotIn("embedding", data)
        self.assertIn("content", data)
        self.assertIn("metadata", data)

    @patch.object(VectorRecordListAPIView, "get_queryset")
    def test_list_is_paginated_filtered_and_omits_embedding(self, get_queryset):
        destination_id = uuid4()
        source_id = uuid4()
        queryset = FakeVectorQuerySet(
            [
                self._record(source_type=VectorDocument.SourceType.ATTRACTION),
                self._record(source_type=VectorDocument.SourceType.CUISINE),
            ]
        )
        get_queryset.return_value = queryset
        request = self._authenticate(
            self.factory.get(
                "/api/v1/admin/vector-store/records/",
                {
                    "source_type": "attractions,cuisine",
                    "destination_id": str(destination_id),
                    "source_id": str(source_id),
                    "page_size": 1,
                },
            )
        )

        response = VectorRecordListAPIView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertNotIn("embedding", response.data["data"][0])
        self.assertEqual(
            queryset.filters,
            [
                {
                    "source_type__in": [
                        VectorDocument.SourceType.ATTRACTION,
                        VectorDocument.SourceType.CUISINE,
                    ]
                },
                {"metadata__destination_id": str(destination_id)},
                {"source_id": source_id},
            ],
        )

    @patch("vector_store.api.v1.admin.views.DestinationVectorService.search")
    def test_semantic_search_returns_content_metadata_and_distance(self, search):
        destination_id = uuid4()
        record_id = uuid4()
        source_id = uuid4()
        search.return_value = [
            VectorSearchResult(
                id=str(record_id),
                source_type=VectorDocument.SourceType.ACTIVITY,
                source_id=str(source_id),
                content="A highly relevant activity.",
                metadata={"destination_id": str(destination_id)},
                distance=0.12,
            )
        ]
        request = self._authenticate(
            self.factory.get(
                "/api/v1/admin/vector-store/search/",
                {
                    "query": "adventurous city activity",
                    "source_type": "activities",
                    "destination_id": str(destination_id),
                    "limit": 5,
                },
            )
        )

        response = VectorSemanticSearchAPIView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        search.assert_called_once_with(
            "adventurous city activity",
            limit=5,
            source_types=[VectorDocument.SourceType.ACTIVITY],
            destination_id=destination_id,
        )
        result = response.data["data"][0]
        self.assertEqual(result["content"], "A highly relevant activity.")
        self.assertEqual(result["metadata"]["destination_id"], str(destination_id))
        self.assertEqual(result["distance"], 0.12)
        self.assertNotIn("embedding", result)

    @patch.object(VectorRecordDeleteAPIView, "get_object")
    def test_single_delete_uses_vector_database(self, get_object):
        record_id = uuid4()
        record = Mock(id=record_id)
        get_object.return_value = record
        request = self._authenticate(
            self.factory.delete(
                f"/api/v1/admin/vector-store/records/{record_id}/delete/",
            )
        )

        response = VectorRecordDeleteAPIView.as_view()(
            request,
            record_id=record_id,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        record.delete.assert_called_once_with(using="vector")
        self.assertEqual(response.data["data"]["id"], str(record_id))

    @patch("vector_store.api.v1.admin.views.transaction.atomic")
    @patch("vector_store.api.v1.admin.views.VectorDocument.objects")
    def test_bulk_delete_deduplicates_and_reports_missing_ids(
        self,
        vector_document_objects,
        atomic,
    ):
        existing_id = uuid4()
        missing_id = uuid4()
        filtered_queryset = vector_document_objects.using.return_value.filter.return_value
        queryset = filtered_queryset.select_for_update.return_value
        queryset.values_list.return_value = [existing_id]
        queryset.delete.return_value = (1, {"vector_store.VectorDocument": 1})
        request = self._authenticate(
            self.factory.post(
                "/api/v1/admin/vector-store/records/bulk-delete/",
                {"ids": [str(existing_id), str(existing_id), str(missing_id)]},
                format="json",
            )
        )

        response = VectorRecordBulkDeleteAPIView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        atomic.assert_called_once_with(using="vector")
        vector_document_objects.using.assert_called_once_with("vector")
        vector_document_objects.using.return_value.filter.assert_called_once_with(
            id__in=[existing_id, missing_id],
        )
        filtered_queryset.select_for_update.assert_called_once_with()
        self.assertEqual(
            response.data["data"],
            {
                "requested_count": 2,
                "deleted_count": 1,
                "deleted_ids": [str(existing_id)],
                "not_found_ids": [str(missing_id)],
            },
        )

    @patch.object(VectorRecordListAPIView, "get_queryset")
    def test_vector_admin_apis_require_a_superadmin(self, get_queryset):
        request = self.factory.get("/api/v1/admin/vector-store/records/")
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, is_superuser=False),
        )

        response = VectorRecordListAPIView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        get_queryset.assert_not_called()
