import socket

from django.db import migrations, models
import django.db.models.deletion


IP_KEYS = ("ip", "ip_address")
LOCATION_KEYS = ("detected_location", "location")
COUNTRY_KEYS = ("detected_country", "detected_country_code", "country")


def _extract(metadata, keys):
    if not isinstance(metadata, dict):
        return ""

    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _clean_ip(value):
    if not value:
        return None

    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, value)
            return value
        except OSError:
            continue

    return None


def migrate_visitor_columns(apps, schema_editor):
    """Fold per-session visitor_id/user_metadata into ChatbotVisitor rows."""
    ChatSession = apps.get_model("chat_session", "ChatSession")
    ChatbotVisitor = apps.get_model("chatbot", "ChatbotVisitor")
    ChatbotBlockedVisitor = apps.get_model(
        "chat_session",
        "ChatbotBlockedVisitor",
    )

    visitor_specs = {}
    session_keys = {}

    rows = (
        ChatSession.objects.filter(is_test=False)
        .exclude(legacy_visitor_id="")
        .order_by("created_at", "id")
        .values(
            "id",
            "chatbot_id",
            "legacy_visitor_id",
            "user_metadata",
            "lead_id",
        )
        .iterator(chunk_size=1000)
    )

    for row in rows:
        key = (
            row["chatbot_id"],
            row["legacy_visitor_id"],
        )

        session_keys[row["id"]] = key

        if key in visitor_specs:
            if row["lead_id"] and not visitor_specs[key]["lead_id"]:
                visitor_specs[key]["lead_id"] = row["lead_id"]
            continue

        metadata = (
            row["user_metadata"]
            if isinstance(row["user_metadata"], dict)
            else {}
        )

        visitor_specs[key] = {
            "chatbot_id": row["chatbot_id"],
            "visitor_id": row["legacy_visitor_id"],
            "lead_id": row["lead_id"],
            "metadata": metadata,
            "ip_address": _clean_ip(
                _extract(metadata, IP_KEYS)
            ),
            "detected_location": _extract(
                metadata,
                LOCATION_KEYS,
            ),
            "detected_country": _extract(
                metadata,
                COUNTRY_KEYS,
            ),
        }

    visitor_ids = {}

    for key, spec in visitor_specs.items():
        visitor = ChatbotVisitor.objects.create(**spec)
        visitor_ids[key] = visitor.pk

    session_ids_by_key = {}

    for session_id, key in session_keys.items():
        session_ids_by_key.setdefault(key, []).append(session_id)

    for key, session_ids in session_ids_by_key.items():
        ChatSession.objects.filter(
            pk__in=session_ids,
        ).update(
            visitor_id=visitor_ids[key],
        )

    for blocked in ChatbotBlockedVisitor.objects.all():
        key = (
            blocked.chatbot_id,
            blocked.legacy_visitor_id,
        )

        visitor_pk = visitor_ids.get(key)

        if visitor_pk is None:
            visitor_pk = ChatbotVisitor.objects.create(
                chatbot_id=blocked.chatbot_id,
                visitor_id=blocked.legacy_visitor_id,
                metadata={},
            ).pk

            visitor_ids[key] = visitor_pk

        ChatbotBlockedVisitor.objects.filter(
            pk=blocked.pk,
        ).update(
            visitor_id=visitor_pk,
        )


def restore_legacy_visitor_columns(apps, schema_editor):
    """Best-effort reverse: rebuild legacy columns from visitor rows."""
    ChatSession = apps.get_model("chat_session", "ChatSession")
    ChatbotBlockedVisitor = apps.get_model(
        "chat_session",
        "ChatbotBlockedVisitor",
    )

    for session in (
        ChatSession.objects
        .exclude(visitor=None)
        .select_related("visitor")
    ):
        session.legacy_visitor_id = session.visitor.visitor_id
        session.user_metadata = session.visitor.metadata or {}

        session.save(
            update_fields=[
                "legacy_visitor_id",
                "user_metadata",
            ],
        )

    for blocked in (
        ChatbotBlockedVisitor.objects
        .exclude(visitor=None)
        .select_related("visitor__chatbot")
    ):
        blocked.legacy_visitor_id = blocked.visitor.visitor_id
        blocked.chatbot_id = blocked.visitor.chatbot_id

        blocked.save(
            update_fields=[
                "legacy_visitor_id",
                "chatbot_id",
            ],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("chat_session", "0007_chatsession_is_test"),
        ("chatbot", "0019_chatbotvisitor"),
    ]

    operations = [
        migrations.RenameField(
            model_name="chatsession",
            old_name="visitor_id",
            new_name="legacy_visitor_id",
        ),
        migrations.RenameField(
            model_name="chatbotblockedvisitor",
            old_name="visitor_id",
            new_name="legacy_visitor_id",
        ),
        migrations.AddField(
            model_name="chatsession",
            name="visitor",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                help_text=(
                    "Anonymous visitor identity correlating sessions from "
                    "the same widget cookie. Ignored once `lead` is set. "
                    "Null for channel sessions identified by "
                    "external_thread_id and for test sessions."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="chat_sessions",
                to="chatbot.chatbotvisitor",
            ),
        ),
        migrations.AddField(
            model_name="chatbotblockedvisitor",
            name="visitor",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="block_records",
                to="chatbot.chatbotvisitor",
            ),
        ),
        migrations.RunPython(
            migrate_visitor_columns,
            reverse_code=restore_legacy_visitor_columns,
        ),
    ]