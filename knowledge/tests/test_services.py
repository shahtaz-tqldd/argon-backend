import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from rest_framework import serializers

from knowledge.models import KnowledgeBase
from knowledge.services.extraction import extract_file_content
from knowledge.services.storage import PrivateKnowledgeStorage
from knowledge.services.training import (
    KnowledgeTrainingService,
    content_digest,
    split_knowledge_content,
    token_count,
)
from knowledge.services.validation import (
    validate_custom_text,
    validate_knowledge_file,
    validate_public_url,
)
from knowledge.utils.choices import KnowledgeSourceTypes


class KnowledgeValidationTests(SimpleTestCase):
    @override_settings(KNOWLEDGE_MAX_TEXT_WORDS=3)
    def test_custom_text_is_limited_by_words(self):
        self.assertEqual(validate_custom_text(" one   two three "), "one   two three")
        with self.assertRaises(serializers.ValidationError):
            validate_custom_text("one two three four")

    @override_settings(
        KNOWLEDGE_MAX_FILE_SIZE_MB=1,
        KNOWLEDGE_MAX_CSV_ROWS=2,
    )
    def test_csv_row_limit_is_enforced_and_stream_is_rewound(self):
        upload = SimpleUploadedFile(
            "records.csv",
            b"name,value\none,1\ntwo,2\n",
            content_type="text/csv",
        )
        with self.assertRaises(serializers.ValidationError):
            validate_knowledge_file(upload)
        self.assertEqual(upload.tell(), 0)

    @override_settings(
        KNOWLEDGE_MAX_FILE_SIZE_MB=1,
        KNOWLEDGE_MAX_STRUCTURED_ITEMS=2,
    )
    def test_json_item_limit_is_enforced(self):
        upload = SimpleUploadedFile(
            "records.json",
            json.dumps({"one": 1, "two": [2]}).encode(),
            content_type="application/json",
        )
        with self.assertRaises(serializers.ValidationError):
            validate_knowledge_file(upload)

    @patch(
        "knowledge.services.validation.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("127.0.0.1", 80))],
    )
    def test_url_validation_blocks_private_networks(self, _getaddrinfo):
        with self.assertRaises(serializers.ValidationError):
            validate_public_url("http://internal.example/path")


class PrivateStorageTests(SimpleTestCase):
    @override_settings(R2_FILES_PREFIX="files")
    def test_knowledge_object_keys_use_the_files_prefix(self):
        key = PrivateKnowledgeStorage.build_key(
            chatbot_id="chatbot-id",
            filename="support handbook.pdf",
        )

        self.assertTrue(key.startswith("files/chatbot-id/"))
        self.assertTrue(key.endswith("/support_handbook.pdf"))

    @override_settings(
        R2_BUCKET_NAME="argon-chatbot",
        R2_PRESIGNED_URL_TTL=600,
    )
    def test_upload_is_private_and_link_is_presigned(self):
        client = Mock()
        client.generate_presigned_url.return_value = "https://signed.example/file"
        storage = PrivateKnowledgeStorage(client=client)
        upload = SimpleUploadedFile("source.csv", b"a,b\n1,2")

        storage.upload(upload, key="files/bot/source.csv", content_type="text/csv")
        url = storage.private_url("files/bot/source.csv")

        extra_args = client.upload_fileobj.call_args.kwargs["ExtraArgs"]
        self.assertEqual(extra_args["ContentType"], "text/csv")
        self.assertNotIn("ServerSideEncryption", extra_args)
        self.assertNotIn("ACL", extra_args)
        self.assertEqual(url, "https://signed.example/file")
        self.assertEqual(
            client.generate_presigned_url.call_args.kwargs["ExpiresIn"],
            600,
        )


class ExtractionAndChunkingTests(SimpleTestCase):
    def test_json_source_is_extracted_after_private_download(self):
        payload = b'{"product": "Argon", "features": ["search", "chat"]}'

        class Storage:
            def download(self, key, file_obj):
                self.assert_key = key
                file_obj.write(payload)
                file_obj.flush()
                file_obj.seek(0)

        knowledge_base = SimpleNamespace(
            file_type="json",
            file_key="files/source.json",
        )
        content = extract_file_content(knowledge_base, storage=Storage())
        self.assertIn('"product": "Argon"', content)
        self.assertIn('"search"', content)

    def test_langchain_splitter_uses_400_tokens_and_50_token_overlap(self):
        content = " ".join(f"knowledge{i}" for i in range(1000))
        chunks = split_knowledge_content(content)
        counts = [token_count(chunk) for chunk in chunks]

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(count <= 400 for count in counts))

        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        first = encoding.encode(chunks[0])
        second = encoding.encode(chunks[1])
        self.assertEqual(first[-50:], second[:50])
        self.assertEqual(content_digest(content), content_digest(content))


class KnowledgeModelTests(SimpleTestCase):
    def test_source_name_falls_back_to_the_right_value(self):
        source = KnowledgeBase(
            source_type=KnowledgeSourceTypes.FILE,
            original_filename="handbook.pdf",
        )
        self.assertEqual(source.name, "handbook.pdf")


class KnowledgeTrainingNotificationTests(SimpleTestCase):
    @patch("knowledge.services.training.create_chatbot_notification")
    def test_training_completion_creates_chatbot_notification(self, create):
        chatbot = object()
        knowledge_base = SimpleNamespace(
            id=uuid4(),
            chatbot=chatbot,
            name="Support handbook",
            source_type=KnowledgeSourceTypes.FILE,
        )
        training_log = SimpleNamespace(
            id=uuid4(),
            force_retrain=False,
        )

        KnowledgeTrainingService._notify_training_complete(
            knowledge_base,
            training_log,
            training_stage="completed",
        )

        create.assert_called_once()
        kwargs = create.call_args.kwargs
        self.assertIs(kwargs["chatbot"], chatbot)
        self.assertEqual(kwargs["notification_type"], "training_complete")
        self.assertEqual(
            kwargs["metadata"]["knowledge_base_id"],
            str(knowledge_base.id),
        )
        self.assertEqual(
            kwargs["metadata"]["training_log_id"],
            str(training_log.id),
        )
