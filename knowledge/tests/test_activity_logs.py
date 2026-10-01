from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase

from knowledge.api.v1.client import views
from knowledge.api.v1.client.serializers import KNOWLEDGE_API_TYPE_TO_SOURCE_TYPE


class KnowledgeActivityTests(SimpleTestCase):
    def setUp(self):
        self.source = SimpleNamespace(
            id=uuid4(), chatbot=Mock(), title="Old title", name="Source",
            source_type=KNOWLEDGE_API_TYPE_TO_SOURCE_TYPE["custom"],
            url=None, original_filename="", text_content="Old content",
            is_enabled=True, delete=Mock(),
        )
        self.request = SimpleNamespace(user=Mock(), data={"title": "New title"})

    @patch.object(views, "KnowledgeBaseSerializer")
    @patch.object(views, "record_chatbot_activity")
    def test_update_snapshots_only_changed_fields(self, record, representation):
        view = views.KnowledgeUpdateAPIView()
        view.get_knowledge_base = Mock(return_value=self.source)
        view.get_knowledge_query = Mock(return_value={"type": "custom"})
        serializer = Mock(validated_data={"title": "New title", "is_enabled": True})
        def save():
            self.source.title = "New title"
            return self.source
        serializer.save.side_effect = save
        view.get_serializer = Mock(return_value=serializer)
        view.patch(self.request)
        recorded = record.call_args.kwargs
        self.assertEqual(recorded["module"], "knowledge")
        self.assertEqual(recorded["action"], "knowledge.updated")
        self.assertEqual(recorded["metadata"]["changes"], {
            "title": {"previous_value": "Old title", "updated_value": "New title"},
        })

    @patch.object(views, "has_active_training", return_value=False)
    @patch.object(views, "record_chatbot_activity")
    def test_custom_content_uses_persisted_content_snapshot(self, record, active):
        view = views.KnowledgeUpdateAPIView()
        view.get_knowledge_base = Mock(return_value=self.source)
        view.get_knowledge_query = Mock(return_value={"type": "custom"})
        view._queue_retraining = Mock()
        serializer = Mock(validated_data={"content": "New content"})

        def save():
            self.source.text_content = "New content"
            return self.source

        serializer.save.side_effect = save
        view.get_serializer = Mock(return_value=serializer)
        view.patch(self.request)
        self.assertEqual(record.call_args.kwargs["metadata"]["changes"], {
            "content": {"previous_value": "Old content", "updated_value": "New content"},
        })
        view._queue_retraining.assert_called_once()

    @patch.object(views, "has_active_training", return_value=False)
    @patch.object(views, "record_chatbot_activity")
    def test_delete_retains_source_identity(self, record, active):
        view = views.KnowledgeDeleteAPIView()
        view.get_knowledge_base = Mock(return_value=self.source)
        view.delete(self.request)
        self.source.delete.assert_called_once()
        recorded = record.call_args.kwargs
        self.assertEqual(recorded["module"], "knowledge")
        self.assertEqual(recorded["action"], "knowledge.deleted")
        self.assertEqual(recorded["metadata"]["knowledge_base_id"], str(self.source.id))

    @patch.object(views, "has_active_training", return_value=True)
    @patch.object(views, "record_chatbot_activity")
    def test_rejected_deletion_does_not_record_activity(self, record, active):
        view = views.KnowledgeDeleteAPIView()
        view.get_knowledge_base = Mock(return_value=self.source)
        response = view.delete(self.request)
        self.assertEqual(response.status_code, 409)
        record.assert_not_called()
        self.source.delete.assert_not_called()

    @patch.object(views, "KnowledgeTrainingLogSerializer")
    @patch.object(views, "KnowledgeBaseSerializer")
    @patch.object(views, "queue_knowledge_training")
    @patch.object(views, "get_knowledge_subscription")
    @patch.object(views, "record_chatbot_activity")
    def test_upload_records_source(self, record, subscription, queue, source_data, training_data):
        view = views.KnowledgeUploadAPIView()
        view.get_chatbot = Mock(return_value=self.source.chatbot)
        view.get_serializer = Mock(return_value=Mock(save=Mock(return_value=self.source)))
        view.get_serializer_context = Mock(return_value={})
        response = view.post(self.request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(record.call_args.kwargs["module"], "knowledge")
        self.assertEqual(record.call_args.kwargs["action"], "knowledge.uploaded")
