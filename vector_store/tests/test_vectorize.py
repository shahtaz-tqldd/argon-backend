import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.db.models import CASCADE

from knowledge.models import KnowledgeBase
from vector_store.models import VectorDocument
from vector_store.services.vectorize import (
    KnowledgeVectorService,
    _RankedCandidate,
)


def _document(name):
    chatbot_id = uuid4()
    return SimpleNamespace(
        id=uuid4(),
        knowledge_base_id=uuid4(),
        knowledge_base=SimpleNamespace(chatbot_id=chatbot_id),
        chunk_index=0,
        token_count=10,
        content=name,
        metadata={"title": name},
    )


class KnowledgeHybridSearchTests(SimpleTestCase):
    def setUp(self):
        self.service = KnowledgeVectorService(embedding_service=Mock())

    def test_vector_document_uses_cascading_knowledge_base_foreign_key(self):
        field = VectorDocument._meta.get_field("knowledge_base")
        self.assertIs(field.remote_field.model, KnowledgeBase)
        self.assertIs(field.remote_field.on_delete, CASCADE)
        self.assertNotIn(
            "chatbot_id",
            {item.name for item in VectorDocument._meta.fields},
        )

    @patch.object(KnowledgeVectorService, "_is_vectorization_enabled", return_value=True)
    def test_search_runs_vector_and_text_retrieval_in_parallel(self, _enabled):
        barrier = threading.Barrier(2, timeout=2)

        def vector_search(*args, **kwargs):
            barrier.wait()
            return [_RankedCandidate(_document("vector"), distance=0.1)]

        def text_search(*args, **kwargs):
            barrier.wait()
            return [_RankedCandidate(_document("text"), text_rank=0.9)]

        with (
            patch.object(self.service, "_vector_candidates", side_effect=vector_search),
            patch.object(
                self.service,
                "_full_text_candidates",
                side_effect=text_search,
            ),
        ):
            results = self.service.search("refund policy")

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.knowledge_base_id for result in results))

    @patch.object(KnowledgeVectorService, "_is_vectorization_enabled", return_value=True)
    def test_search_passes_knowledge_filters_to_both_branches(self, _enabled):
        chatbot_id = uuid4()
        knowledge_base_ids = [uuid4(), uuid4()]
        with (
            patch.object(self.service, "_vector_candidates", return_value=[]) as vector,
            patch.object(
                self.service,
                "_full_text_candidates",
                return_value=[],
            ) as text,
        ):
            self.service.search(
                "pricing",
                chatbot_id=chatbot_id,
                knowledge_base_ids=knowledge_base_ids,
            )

        expected = {
            "chatbot_id": chatbot_id,
            "knowledge_base_ids": tuple(knowledge_base_ids),
        }
        vector.assert_called_once_with("pricing", **expected)
        text.assert_called_once_with("pricing", **expected)

    def test_rrf_rewards_chunks_returned_by_both_searches(self):
        shared = _document("shared")
        results = self.service._reciprocal_rank_fusion(
            [
                _RankedCandidate(_document("vector-only"), distance=0.05),
                _RankedCandidate(shared, distance=0.1),
            ],
            [
                _RankedCandidate(_document("text-only"), text_rank=0.9),
                _RankedCandidate(shared, text_rank=0.8),
            ],
            limit=3,
        )

        self.assertEqual(results[0].id, str(shared.id))
        self.assertEqual(results[0].distance, 0.1)
        self.assertEqual(results[0].text_rank, 0.8)

    def test_complete_vector_set_checks_recorded_chunk_count(self):
        queryset = Mock()
        queryset.count.return_value = 3
        queryset.order_by.return_value.values_list.return_value.first.return_value = {
            "chunk_count": 3
        }
        objects = Mock()
        objects.filter.return_value = queryset

        with patch("vector_store.services.vectorize.VectorDocument.objects", objects):
            complete = self.service.has_complete_vector_set(uuid4())

        self.assertTrue(complete)

    def test_replacing_vectors_is_atomic_and_knowledge_scoped(self):
        knowledge_base_id = uuid4()
        documents = [Mock(), Mock()]
        objects = Mock()
        queryset = objects.filter.return_value

        with (
            patch("vector_store.services.vectorize.VectorDocument.objects", objects),
            patch("vector_store.services.vectorize.transaction.atomic"),
        ):
            self.service.replace_knowledge_base(knowledge_base_id, documents)

        objects.filter.assert_called_once_with(
            knowledge_base_id=knowledge_base_id
        )
        queryset.delete.assert_called_once_with()
        objects.bulk_create.assert_called_once_with(
            documents,
            batch_size=100,
        )
