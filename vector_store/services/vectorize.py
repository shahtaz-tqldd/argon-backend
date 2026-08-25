import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import close_old_connections, transaction
from django.db.models import F
from pgvector.django import CosineDistance

from app.base.models import ArgonChatbotConfig
from vector_store.models import VectorDocument

from .gemini_embeddings import GeminiEmbeddingService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VectorSearchResult:
    id: str
    knowledge_base_id: str
    chatbot_id: str
    chunk_index: int
    token_count: int
    content: str
    metadata: dict
    distance: float | None = None
    text_rank: float | None = None
    rrf_score: float = 0.0


@dataclass(slots=True)
class _RankedCandidate:
    document: VectorDocument
    distance: float | None = None
    text_rank: float | None = None


class KnowledgeVectorService:
    """Persistence and hybrid search for knowledge-base vector chunks."""

    candidate_limit = 20
    rrf_rank_constant = 60

    def __init__(self, *, embedding_service=None):
        self.embedding_service = embedding_service

    def _get_embedding_service(self):
        if self.embedding_service is None:
            self.embedding_service = GeminiEmbeddingService()
        return self.embedding_service

    @staticmethod
    def _is_vectorization_enabled():
        enabled = (
            ArgonChatbotConfig.objects.order_by()
            .values_list("is_vectorize_enabled", flat=True)
            .first()
        )
        return True if enabled is None else enabled

    @classmethod
    def get_indexed_knowledge_base_ids(cls, knowledge_base_ids):
        ids = list(knowledge_base_ids)
        if not ids or not cls._is_vectorization_enabled():
            return set()
        return set(
            VectorDocument.objects.filter(knowledge_base_id__in=ids)
            .order_by()
            .values_list("knowledge_base_id", flat=True)
            .distinct()
        )

    def replace_knowledge_base(self, knowledge_base_id, documents):
        with transaction.atomic():
            VectorDocument.objects.filter(
                knowledge_base_id=knowledge_base_id,
            ).delete()
            return VectorDocument.objects.bulk_create(
                documents,
                batch_size=100,
            )

    def remove_knowledge_base(self, knowledge_base_id):
        return VectorDocument.objects.filter(
            knowledge_base_id=knowledge_base_id,
        ).delete()

    def remove_chatbot(self, chatbot_id):
        return VectorDocument.objects.filter(
            knowledge_base__chatbot_id=chatbot_id,
        ).delete()

    def has_complete_vector_set(self, knowledge_base_id):
        queryset = VectorDocument.objects.filter(
            knowledge_base_id=knowledge_base_id,
        )
        count = queryset.count()
        if count == 0:
            return False
        metadata = queryset.order_by("chunk_index").values_list(
            "metadata",
            flat=True,
        ).first()
        try:
            return count == int((metadata or {}).get("chunk_count", 0))
        except (TypeError, ValueError):
            return False

    def search(
        self,
        query,
        *,
        limit=10,
        chatbot_id=None,
        knowledge_base_ids=None,
    ):
        if not self._is_vectorization_enabled():
            return []
        limit = max(int(limit), 0)
        if limit == 0:
            return []

        filters = {
            "chatbot_id": chatbot_id,
            "knowledge_base_ids": tuple(knowledge_base_ids)
            if knowledge_base_ids
            else None,
        }
        searches = {
            "vector": lambda: self._vector_candidates(query, **filters),
            "full_text": lambda: self._full_text_candidates(query, **filters),
        }
        candidates = {}
        errors = {}
        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="knowledge-hybrid-search",
        ) as executor:
            futures = {
                name: executor.submit(self._run_search, search)
                for name, search in searches.items()
            }
            for name, future in futures.items():
                try:
                    candidates[name] = future.result()
                except Exception as exc:
                    errors[name] = exc
                    logger.warning(
                        "Knowledge %s search failed; using remaining results. "
                        "error_type=%s",
                        name,
                        type(exc).__name__,
                    )
        if len(errors) == len(searches):
            raise errors["vector"]
        return self._reciprocal_rank_fusion(
            candidates.get("vector", []),
            candidates.get("full_text", []),
            limit=limit,
        )

    @staticmethod
    def _run_search(search):
        close_old_connections()
        try:
            return search()
        finally:
            close_old_connections()

    def _vector_candidates(
        self,
        query,
        *,
        chatbot_id=None,
        knowledge_base_ids=None,
    ):
        query_embedding = self._get_embedding_service().embed_query(query)
        queryset = VectorDocument.objects.select_related(
            "knowledge_base"
        ).annotate(distance=CosineDistance("embedding", query_embedding))
        queryset = self._apply_search_filters(
            queryset,
            chatbot_id=chatbot_id,
            knowledge_base_ids=knowledge_base_ids,
        )
        documents = list(
            queryset.order_by("distance", "-updated_at")[: self.candidate_limit]
        )
        return [
            _RankedCandidate(document=item, distance=float(item.distance))
            for item in documents
        ]

    def _full_text_candidates(
        self,
        query,
        *,
        chatbot_id=None,
        knowledge_base_ids=None,
    ):
        search_query = SearchQuery(
            query,
            config="english",
            search_type="websearch",
        )
        queryset = (
            VectorDocument.objects.select_related("knowledge_base")
            .filter(search_vector=search_query)
            .annotate(
                text_rank=SearchRank(
                    F("search_vector"),
                    search_query,
                    cover_density=True,
                )
            )
        )
        queryset = self._apply_search_filters(
            queryset,
            chatbot_id=chatbot_id,
            knowledge_base_ids=knowledge_base_ids,
        )
        documents = list(
            queryset.order_by("-text_rank", "-updated_at")[: self.candidate_limit]
        )
        return [
            _RankedCandidate(document=item, text_rank=float(item.text_rank))
            for item in documents
        ]

    @staticmethod
    def _apply_search_filters(
        queryset,
        *,
        chatbot_id=None,
        knowledge_base_ids=None,
    ):
        queryset = queryset.filter(
            knowledge_base__is_enabled=True,
            knowledge_base__chatbot__is_deleted=False,
        )
        if chatbot_id:
            queryset = queryset.filter(
                knowledge_base__chatbot_id=chatbot_id
            )
        if knowledge_base_ids:
            queryset = queryset.filter(
                knowledge_base_id__in=knowledge_base_ids
            )
        return queryset

    def _reciprocal_rank_fusion(
        self,
        vector_candidates,
        text_candidates,
        *,
        limit,
    ):
        fused = {}
        for candidates in (vector_candidates, text_candidates):
            for rank, candidate in enumerate(candidates, start=1):
                document_id = str(candidate.document.id)
                entry = fused.setdefault(
                    document_id,
                    {
                        "document": candidate.document,
                        "distance": None,
                        "text_rank": None,
                        "score": 0.0,
                        "best_rank": rank,
                    },
                )
                entry["score"] += 1.0 / (self.rrf_rank_constant + rank)
                entry["best_rank"] = min(entry["best_rank"], rank)
                if candidate.distance is not None:
                    entry["distance"] = candidate.distance
                if candidate.text_rank is not None:
                    entry["text_rank"] = candidate.text_rank

        ranked = sorted(
            fused.values(),
            key=lambda item: (
                -item["score"],
                item["best_rank"],
                str(item["document"].id),
            ),
        )[:limit]
        return [
            VectorSearchResult(
                id=str(item["document"].id),
                knowledge_base_id=str(item["document"].knowledge_base_id),
                chatbot_id=str(item["document"].knowledge_base.chatbot_id),
                chunk_index=item["document"].chunk_index,
                token_count=item["document"].token_count,
                content=item["document"].content,
                metadata=item["document"].metadata,
                distance=item["distance"],
                text_rank=item["text_rank"],
                rrf_score=item["score"],
            )
            for item in ranked
        ]
