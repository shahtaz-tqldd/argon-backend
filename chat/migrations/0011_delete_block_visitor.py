from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("chat_session", "0010_merge_block_records"),
        ("chatbot", "0020_chatbotvisitor_blocking"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ChatbotBlockedVisitor",
        ),
    ]