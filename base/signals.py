from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from app.services.r2 import R2Storage, schedule_delete_image


@receiver(post_delete, sender="accounts.UserProfile")
@receiver(post_delete, sender="workspace.Workspace")
@receiver(post_delete, sender="chatbot.Chatbot")
@receiver(post_delete, sender="base.ArgonChatbotConfig")
def delete_public_asset_with_record(sender, instance, **kwargs):
    url_field = "avatar_url" if sender._meta.label_lower == "accounts.userprofile" else "logo"
    schedule_delete_image(image_url=getattr(instance, url_field, ""))


@receiver(post_delete, sender="knowledge.KnowledgeBase")
def delete_private_knowledge_file_with_record(sender, instance, **kwargs):
    if not instance.file_key:
        return
    key = instance.file_key
    transaction.on_commit(lambda: R2Storage().delete(key))
