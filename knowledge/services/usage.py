from django.db.models import Sum

from knowledge.models import KnowledgeBase
from vector_store.models import VectorDocument


KNOWLEDGE_CHUNK_LIMIT = 500
KNOWLEDGE_FILE_SIZE_LIMIT_MB = 25
BYTES_PER_MEGABYTE = 1024 * 1024


def get_knowledge_usage(chatbot):
    total_file_size_bytes = KnowledgeBase.objects.filter(
        chatbot_id=chatbot.id,
    ).aggregate(
        total=Sum("file_size", default=0),
    )["total"]
    total_chunks = VectorDocument.objects.filter(
        knowledge_base__chatbot_id=chatbot.id,
    ).count()

    return {
        "total_chunks": total_chunks,
        "chunk_limit": KNOWLEDGE_CHUNK_LIMIT,
        "total_file_size_bytes": total_file_size_bytes,
        "file_size_limit_bytes": (
            KNOWLEDGE_FILE_SIZE_LIMIT_MB * BYTES_PER_MEGABYTE
        ),
        "file_size_limit_mb": KNOWLEDGE_FILE_SIZE_LIMIT_MB,
    }
