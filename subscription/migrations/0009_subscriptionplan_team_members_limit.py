from django.db import migrations, models


def backfill_team_members_limit(apps, schema_editor):
    """Add the team_members_limit key to existing contract snapshots.

    Snapshots are immutable from application code, so the schema change
    backfills the new limit here. Existing contracts were signed before the
    limit existed, so they receive null (unlimited team members).
    """
    ChatbotSubscription = apps.get_model("subscription", "ChatbotSubscription")
    subscriptions = []
    for subscription in ChatbotSubscription.objects.using(
        schema_editor.connection.alias
    ).iterator():
        snapshot = subscription.snapshot
        if not isinstance(snapshot, dict):
            continue
        limits = snapshot.get("limits")
        if not isinstance(limits, dict) or "team_members_limit" in limits:
            continue
        limits["team_members_limit"] = None
        subscriptions.append(subscription)

    if subscriptions:
        ChatbotSubscription.objects.using(
            schema_editor.connection.alias
        ).bulk_update(subscriptions, ["snapshot"])


def remove_team_members_limit(apps, schema_editor):
    ChatbotSubscription = apps.get_model("subscription", "ChatbotSubscription")
    subscriptions = []
    for subscription in ChatbotSubscription.objects.using(
        schema_editor.connection.alias
    ).iterator():
        snapshot = subscription.snapshot
        if not isinstance(snapshot, dict):
            continue
        limits = snapshot.get("limits")
        if not isinstance(limits, dict) or "team_members_limit" not in limits:
            continue
        limits.pop("team_members_limit")
        subscriptions.append(subscription)

    if subscriptions:
        ChatbotSubscription.objects.using(
            schema_editor.connection.alias
        ).bulk_update(subscriptions, ["snapshot"])


class Migration(migrations.Migration):

    dependencies = [
        ("subscription", "0008_alter_subscriptionplan_features_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriptionplan",
            name="team_members_limit",
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    "Active team members allowed on one chatbot; null means "
                    "unlimited."
                ),
                null=True,
            ),
        ),
        migrations.RunPython(
            backfill_team_members_limit,
            reverse_code=remove_team_members_limit,
        ),
    ]
