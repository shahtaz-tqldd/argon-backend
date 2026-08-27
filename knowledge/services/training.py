import asyncio
import hashlib
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.services.web_scrapper import WebScraper
from knowledge.models import KnowledgeBase, KnowledgeTrainingLog
from knowledge.services.extraction import extract_file_content
from knowledge.services.usage import (
    get_knowledge_subscription,
    validate_knowledge_chunk_capacity,
)
from knowledge.services.validation import validate_public_url
from knowledge.utils.choices import (
    KnowledgeSourceTypes,
    KnowledgeTrainingStageTypes,
    StatusTypes,
)
from notification.models import NotificationType
from notification.services import create_chatbot_notification
from vector_store.models import VectorDocument
from vector_store.services.gemini_embeddings import GeminiEmbeddingService
from vector_store.services.vectorize import KnowledgeVectorService

logger = logging.getLogger(__name__)


def content_digest(content):
    normalized = (content or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def split_knowledge_content(content):
    """Split by model tokens into 400-token chunks with 50-token overlap."""

    from langchain_text_splitters import TokenTextSplitter

    splitter = TokenTextSplitter(
        encoding_name="cl100k_base",
        chunk_size=settings.KNOWLEDGE_CHUNK_SIZE,
        chunk_overlap=settings.KNOWLEDGE_CHUNK_OVERLAP,
        strip_whitespace=True,
    )
    return [chunk for chunk in splitter.split_text(content) if chunk.strip()]


def token_count(content):
    import tiktoken

    return len(tiktoken.get_encoding("cl100k_base").encode(content))


def queue_knowledge_training(knowledge_base, *, force=False):
    """Create a durable job record and submit work to Celery/Redis."""

    from knowledge.tasks import train_knowledge_base

    log = KnowledgeTrainingLog.objects.create(
        knowledge_base=knowledge_base,
        force_retrain=force,
    )
    result = train_knowledge_base.delay(str(log.id))
    log.celery_task_id = result.id or ""
    log.save(update_fields=["celery_task_id", "updated_at"])
    return log


class KnowledgeTrainingService:
    def __init__(
        self,
        *,
        embedding_service=None,
        storage=None,
        scraper=None,
        vector_service=None,
    ):
        self.embedding_service = embedding_service
        self.storage = storage
        self.scraper = scraper or WebScraper()
        self.vector_service = vector_service or KnowledgeVectorService()

    def _get_embedding_service(self):
        if self.embedding_service is None:
            self.embedding_service = GeminiEmbeddingService()
        return self.embedding_service

    def run(self, training_log):
        knowledge_base = KnowledgeBase.objects.select_related("chatbot").get(
            pk=training_log.knowledge_base_id
        )
        self._start(knowledge_base, training_log)
        try:
            get_knowledge_subscription(knowledge_base.chatbot)
            content, scraped_title = self._extract(knowledge_base, training_log)
            title_from_scrape = ""
            if scraped_title and not knowledge_base.title:
                title_from_scrape = scraped_title[:500]
                knowledge_base.title = title_from_scrape
            new_hash = content_digest(content)
            changed = new_hash != knowledge_base.content_hash
            training_log.content_changed = changed
            training_log.save(update_fields=["content_changed", "updated_at"])

            if (
                knowledge_base.content_hash
                and not changed
                and not training_log.force_retrain
                and self.vector_service.has_complete_vector_set(
                    knowledge_base.id
                )
            ):
                self._complete_without_changes(knowledge_base, training_log)
                self._notify_training_complete(
                    knowledge_base,
                    training_log,
                    training_stage=KnowledgeTrainingStageTypes.SKIPPED,
                )
                return training_log

            self._update_log(
                training_log,
                stage=KnowledgeTrainingStageTypes.CHUNKING,
                progress=25,
                message="Splitting extracted content into token-aware chunks.",
            )
            chunks = split_knowledge_content(content)
            if not chunks:
                raise ValueError("No trainable text was found in this source.")

            training_log.total_chunks = len(chunks)
            training_log.save(update_fields=["total_chunks", "updated_at"])
            validate_knowledge_chunk_capacity(knowledge_base, len(chunks))
            documents = self._embed_chunks(
                knowledge_base,
                training_log,
                chunks,
                content_hash=new_hash,
            )

            self._update_log(
                training_log,
                stage=KnowledgeTrainingStageTypes.INDEXING,
                progress=95,
                message="Replacing the source vectors.",
            )
            with transaction.atomic():
                validate_knowledge_chunk_capacity(
                    knowledge_base,
                    len(chunks),
                    lock_subscription=True,
                )
                self.vector_service.replace_knowledge_base(
                    knowledge_base.id,
                    documents,
                )
                completed_at = timezone.now()
                update_fields = {
                    "extracted_content": content,
                    "content_hash": new_hash,
                    "status": StatusTypes.READY,
                    "processed_at": completed_at,
                    "error_message": "",
                    "updated_at": completed_at,
                }
                if title_from_scrape:
                    update_fields["title"] = title_from_scrape
                KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
                    **update_fields
                )
                self._update_log(
                    training_log,
                    stage=KnowledgeTrainingStageTypes.COMPLETED,
                    progress=100,
                    processed_chunks=len(chunks),
                    message=f"Trained {len(chunks)} vector chunks.",
                    completed_at=completed_at,
                )
            self._notify_training_complete(
                knowledge_base,
                training_log,
                training_stage=KnowledgeTrainingStageTypes.COMPLETED,
            )
            return training_log
        except Exception as exc:
            logger.exception("Knowledge training failed for source %s", knowledge_base.id)
            self._fail(knowledge_base, training_log, exc)
            raise

    def _start(self, knowledge_base, training_log):
        now = timezone.now()
        KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
            status=StatusTypes.PROCESSING,
            error_message="",
            updated_at=now,
        )
        self._update_log(
            training_log,
            stage=KnowledgeTrainingStageTypes.EXTRACTING,
            progress=5,
            message="Extracting source content.",
            started_at=now,
        )

    def _extract(self, knowledge_base, training_log):
        if knowledge_base.source_type == KnowledgeSourceTypes.TEXT:
            return knowledge_base.text_content.strip(), ""
        if knowledge_base.source_type == KnowledgeSourceTypes.FILE:
            return (
                extract_file_content(knowledge_base, storage=self.storage),
                "",
            )
        if knowledge_base.source_type == KnowledgeSourceTypes.WEBSITE:
            validate_public_url(knowledge_base.url)
            result = asyncio.run(
                self.scraper.scrape_text_content(knowledge_base.url)
            )
            if not result.success:
                raise ValueError(result.error or "Website extraction failed.")
            KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
                last_crawled_at=timezone.now()
            )
            return (result.data.text or "").strip(), result.data.title or ""
        raise ValueError("Unsupported knowledge source type.")

    def _embed_chunks(
        self,
        knowledge_base,
        training_log,
        chunks,
        *,
        content_hash,
    ):
        documents = []
        total = len(chunks)
        for index, chunk in enumerate(chunks):
            embedding = self._get_embedding_service().embed_document(chunk)
            processed = index + 1
            progress = 30 + int((processed / total) * 60)
            self._update_log(
                training_log,
                stage=KnowledgeTrainingStageTypes.EMBEDDING,
                progress=progress,
                processed_chunks=processed,
                message=f"Embedded chunk {processed} of {total}.",
            )
            documents.append(
                VectorDocument(
                    knowledge_base=knowledge_base,
                    chunk_index=index,
                    token_count=token_count(chunk),
                    content_hash=content_hash,
                    content=chunk,
                    metadata={
                        "knowledge_base_id": str(knowledge_base.id),
                        "chatbot_id": str(knowledge_base.chatbot_id),
                        "knowledge_source_type": knowledge_base.source_type,
                        "title": knowledge_base.title,
                        "url": knowledge_base.url,
                        "original_filename": knowledge_base.original_filename,
                        "chunk_index": index,
                        "chunk_count": total,
                    },
                    embedding=embedding,
                )
            )
        return documents

    @staticmethod
    def _complete_without_changes(knowledge_base, training_log):
        now = timezone.now()
        KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
            status=StatusTypes.READY,
            error_message="",
            processed_at=now,
            updated_at=now,
        )
        KnowledgeTrainingService._update_log(
            training_log,
            stage=KnowledgeTrainingStageTypes.SKIPPED,
            progress=100,
            message="Source content is unchanged; existing vectors were retained.",
            completed_at=now,
        )

    @staticmethod
    def _fail(knowledge_base, training_log, exc):
        now = timezone.now()
        error = str(exc)[:4000]
        KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
            status=StatusTypes.FAILED,
            error_message=error,
            updated_at=now,
        )
        KnowledgeTrainingService._update_log(
            training_log,
            stage=KnowledgeTrainingStageTypes.FAILED,
            message="Training failed.",
            error_message=error,
            completed_at=now,
        )

    @staticmethod
    def _notify_training_complete(
        knowledge_base,
        training_log,
        *,
        training_stage,
    ):
        try:
            create_chatbot_notification(
                chatbot=knowledge_base.chatbot,
                notification_type=NotificationType.TRAINING_COMPLETE,
                title="Knowledge training complete",
                message=f'"{knowledge_base.name}" is trained and ready to use.',
                metadata={
                    "knowledge_base_id": str(knowledge_base.id),
                    "training_log_id": str(training_log.id),
                    "source_type": knowledge_base.source_type,
                    "training_stage": training_stage,
                    "force_retrain": training_log.force_retrain,
                },
            )
        except Exception:
            logger.exception(
                "Failed to create training-complete notification for source %s",
                knowledge_base.id,
            )

    @staticmethod
    def _update_log(training_log, **values):
        values["updated_at"] = timezone.now()
        KnowledgeTrainingLog.objects.filter(pk=training_log.pk).update(**values)
        for key, value in values.items():
            setattr(training_log, key, value)
