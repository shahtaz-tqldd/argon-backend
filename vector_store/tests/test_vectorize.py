import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase

from vector_store.models import VectorDocument
from vector_store.services.vectorize import (
    DestinationVectorService,
    _RankedCandidate,
)


def _document(name):
    return SimpleNamespace(
        id=uuid4(),
        source_type=VectorDocument.SourceType.DESTINATION,
        source_id=uuid4(),
        content=name,
        metadata={"name": name},
    )


class HybridSearchTests(SimpleTestCase):
    def setUp(self):
        self.service = DestinationVectorService(embedding_service=Mock())

    @patch.object(DestinationVectorService, "_is_vectorization_enabled", return_value=True)
    def test_search_runs_vector_and_full_text_retrieval_in_parallel(self, _enabled):
        barrier = threading.Barrier(2, timeout=2)
        vector_document = _document("vector")
        text_document = _document("text")

        def vector_search(*args, **kwargs):
            barrier.wait()
            return [_RankedCandidate(vector_document, distance=0.1)]

        def full_text_search(*args, **kwargs):
            barrier.wait()
            return [_RankedCandidate(text_document, text_rank=0.9)]

        with (
            patch.object(self.service, "_vector_candidates", side_effect=vector_search),
            patch.object(
                self.service,
                "_full_text_candidates",
                side_effect=full_text_search,
            ),
        ):
            results = self.service.search("mountain")

        self.assertEqual(len(results), 2)

    @patch.object(DestinationVectorService, "_is_vectorization_enabled", return_value=True)
    def test_search_defaults_to_top_ten_after_fusion(self, _enabled):
        documents = [_document(f"document-{index}") for index in range(20)]
        vector_candidates = [
            _RankedCandidate(document, distance=index / 100)
            for index, document in enumerate(documents)
        ]
        text_candidates = [
            _RankedCandidate(document, text_rank=1 - (index / 100))
            for index, document in enumerate(documents)
        ]

        with (
            patch.object(
                self.service,
                "_vector_candidates",
                return_value=vector_candidates,
            ),
            patch.object(
                self.service,
                "_full_text_candidates",
                return_value=text_candidates,
            ),
        ):
            results = self.service.search("mountain")

        self.assertEqual(len(results), 10)

    def test_rrf_rewards_documents_returned_by_both_searches(self):
        shared = _document("shared")
        vector_only = _document("vector-only")
        text_only = _document("text-only")

        results = self.service._reciprocal_rank_fusion(
            [
                _RankedCandidate(vector_only, distance=0.05),
                _RankedCandidate(shared, distance=0.1),
            ],
            [
                _RankedCandidate(text_only, text_rank=0.9),
                _RankedCandidate(shared, text_rank=0.8),
            ],
            limit=3,
        )

        self.assertEqual(results[0].id, str(shared.id))
        self.assertEqual(results[0].distance, 0.1)
        self.assertEqual(results[0].text_rank, 0.8)
        self.assertGreater(results[0].rrf_score, results[1].rrf_score)

    def test_each_retrieval_branch_fetches_twenty_candidates(self):
        class FakeQuerySet:
            def __init__(self):
                self.slices = []

            def annotate(self, **kwargs):
                return self

            def filter(self, **kwargs):
                return self

            def order_by(self, *args):
                return self

            def __getitem__(self, item):
                self.slices.append(item)
                return []

        vector_queryset = FakeQuerySet()
        text_queryset = FakeQuerySet()
        objects = Mock()
        objects.using.side_effect = [vector_queryset, text_queryset]

        with patch("vector_store.services.vectorize.VectorDocument.objects", objects):
            self.service.embedding_service.embed_query.return_value = [0.1] * 1536
            self.service._vector_candidates("mountain")
            self.service._full_text_candidates("mountain")

        self.assertEqual(vector_queryset.slices, [slice(None, 20, None)])
        self.assertEqual(text_queryset.slices, [slice(None, 20, None)])
