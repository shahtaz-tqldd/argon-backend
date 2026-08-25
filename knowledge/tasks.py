from celery import shared_task

from knowledge.models import KnowledgeTrainingLog
from knowledge.services.training import KnowledgeTrainingService


@shared_task(
    bind=True,
    autoretry_for=(),
    name="knowledge.tasks.train_knowledge_base",
)
def train_knowledge_base(self, training_log_id):
    training_log = KnowledgeTrainingLog.objects.get(pk=training_log_id)
    if training_log.celery_task_id != self.request.id:
        training_log.celery_task_id = self.request.id or ""
        training_log.save(update_fields=["celery_task_id", "updated_at"])
    KnowledgeTrainingService().run(training_log)
    return {
        "training_log_id": str(training_log.id),
        "knowledge_base_id": str(training_log.knowledge_base_id),
    }
