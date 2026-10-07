from celery import shared_task

from base.socket.services.presence import expire_stale_presence


@shared_task
def sweep_presence():
    """Fallback GC for abnormal disconnects; clean closes are handled inline."""
    return expire_stale_presence()
