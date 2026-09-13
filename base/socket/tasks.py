from celery import shared_task

from base.socket.services.presence import expire_stale_presence


@shared_task
def sweep_presence():
    return expire_stale_presence()
