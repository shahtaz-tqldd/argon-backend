from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from appointment.api.v1.client import views as appointment_views
from appointment.api.v1.client.serializers import AppointmentBookingConfigSerializer, AppointmentUpdateSerializer
from appointment.models import Appointment, AppointmentBookingConfig
from chatbot.models import Chatbot
from lead_capture.api.v1.client import views as lead_views
from lead_capture.api.v1.client.serializers import LeadCaptureConfigSerializer, LeadUpdateSerializer
from lead_capture.models import Lead, LeadCaptureConfig


class ModuleActivityTests(SimpleTestCase):
    def setUp(self):
        self.chatbot = Chatbot(slug="support")
        self.request = SimpleNamespace(user=Mock(), data={})

    def test_updates_snapshot_changed_fields_and_skip_unchanged_fields(self):
        cases = [
            (appointment_views, appointment_views.AppointmentBookingConfigUpdateAPIView,
             AppointmentBookingConfig(chatbot=self.chatbot), AppointmentBookingConfigSerializer,
             "get_config", {"confirmation_message": "New message", "is_enabled": False},
             "appointment", "appointment.config.updated"),
            (appointment_views, appointment_views.AppointmentUpdateAPIView,
             Appointment(chatbot=self.chatbot, starts_at=datetime(2026, 10, 1, tzinfo=timezone.utc)),
             AppointmentUpdateSerializer, "get_appointment", {"notes": "New notes"},
             "appointment", "appointment.updated"),
            (lead_views, lead_views.LeadCaptureConfigUpdateAPIView,
             LeadCaptureConfig(chatbot=self.chatbot), LeadCaptureConfigSerializer,
             None, {"consent_message": "New consent"}, "leads", "leads.config.updated"),
            (lead_views, lead_views.LeadUpdateView,
             Lead(chatbot=self.chatbot, collected_fields={"name": "Old"}), LeadUpdateSerializer,
             "get_lead", {"collected_fields": {"name": "New"}}, "leads", "leads.updated"),
        ]
        for module_views, view_class, instance, serializer_class, getter, changes, module, action in cases:
            with self.subTest(view=view_class.__name__):
                view = view_class()
                if getter:
                    setattr(view, getter, Mock(return_value=instance))
                view.get_chatbot = Mock(return_value=self.chatbot)
                serializer = serializer_class(instance, data=changes, partial=True)
                serializer.is_valid(raise_exception=True)
                before = serializer.to_representation(instance)

                def save():
                    for field, value in changes.items():
                        setattr(instance, field, value)
                    return instance

                serializer.save = Mock(side_effect=save)
                view.get_serializer = Mock(return_value=serializer)
                with patch.object(module_views, "get_object_or_404", return_value=instance), patch.object(module_views, "record_chatbot_activity") as record:
                    response = view.patch(self.request)
                self.assertEqual(response.status_code, 200)
                recorded = record.call_args.kwargs
                self.assertEqual(recorded["module"], module)
                self.assertEqual(recorded["action"], action)
                expected = {
                    field: {"previous_value": before[field], "updated_value": value}
                    for field, value in changes.items() if before[field] != value
                }
                self.assertEqual(recorded["metadata"]["changes"], expected)

    def test_schedule_snapshots_survive_nested_serializer_mutation(self):
        view = appointment_views.AppointmentBookingScheduleUpdateAPIView()
        config = AppointmentBookingConfig(chatbot=self.chatbot)
        view.get_config = Mock(return_value=config)
        previous = {"schedules": [{"weekday": 0, "slots": [{"start_time": "09:00:00"}]}]}
        updated = {"schedules": [{"weekday": 0, "slots": [{"start_time": "10:00:00"}]}]}
        serializer = Mock(validated_data={"schedules": []})
        serializer.to_representation.side_effect = [previous, updated]
        serializer.save.return_value = config
        view.get_serializer = Mock(return_value=serializer)
        with patch.object(appointment_views, "record_chatbot_activity") as record:
            view.patch(self.request)
        self.assertEqual(record.call_args.kwargs["metadata"]["changes"]["schedules"], {
            "previous_value": previous["schedules"], "updated_value": updated["schedules"],
        })

    def test_deletions_retain_identity_before_delete_clears_id(self):
        appointment = Appointment(chatbot=self.chatbot)
        note = SimpleNamespace(id="note-id", lead_id="lead-id", lead=SimpleNamespace(chatbot=self.chatbot), content="Note text")
        for module_views, view_class, instance, getter, action, identity in [
            (appointment_views, appointment_views.AppointmentDeleteAPIView, appointment, "get_appointment", "appointment.deleted", "appointment_id"),
            (lead_views, lead_views.LeadNoteDeleteView, note, "get_lead_note", "leads.note.deleted", "note_id"),
        ]:
            with self.subTest(view=view_class.__name__):
                original_id = str(instance.id)
                instance.delete = Mock(side_effect=lambda: setattr(instance, "id", None))
                view = view_class()
                setattr(view, getter, Mock(return_value=instance))
                with patch.object(module_views, "record_chatbot_activity") as record:
                    view.delete(self.request)
                self.assertEqual(record.call_args.kwargs["action"], action)
                self.assertEqual(record.call_args.kwargs["metadata"][identity], original_id)

    def test_invalid_update_does_not_record_activity(self):
        view = lead_views.LeadUpdateView()
        view.get_lead = Mock(return_value=Lead(chatbot=self.chatbot))
        serializer = Mock()
        serializer.is_valid.side_effect = ValidationError("Invalid")
        view.get_serializer = Mock(return_value=serializer)
        with patch.object(lead_views, "record_chatbot_activity") as record:
            with self.assertRaises(ValidationError):
                view.patch(self.request)
        record.assert_not_called()
        serializer.save.assert_not_called()

    def test_lead_config_creation_records_initial_configuration(self):
        view = lead_views.LeadCaptureConfigCreateAPIView()
        config = LeadCaptureConfig(chatbot=self.chatbot)
        view.get_chatbot = Mock(return_value=self.chatbot)
        serializer = LeadCaptureConfigSerializer(config)
        serializer.is_valid = Mock()
        serializer.save = Mock(return_value=config)
        view.get_serializer = Mock(return_value=serializer)
        with patch.object(lead_views.LeadCaptureConfig.objects, "filter") as query, patch.object(lead_views, "record_chatbot_activity") as record:
            query.return_value.exists.return_value = False
            response = view.post(self.request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(record.call_args.kwargs["action"], "leads.config.created")
        self.assertIn("collectable_fields", record.call_args.kwargs["metadata"]["configuration"])

    def test_export_records_format_count_and_ids(self):
        view = lead_views.ExportLeadAPIView()
        view.get_chatbot = Mock(return_value=self.chatbot)
        view.get_chatbot_query = Mock(return_value={"file_format": "csv"})
        with patch.object(lead_views.Lead.objects, "filter") as query, patch.object(lead_views, "build_lead_export", return_value=b"csv"), patch.object(lead_views, "record_chatbot_activity") as record:
            query.return_value.order_by.return_value.__getitem__.return_value = [SimpleNamespace(id="lead-id")]
            response = view.get(self.request)
        self.assertEqual(response.status_code, 200)
        metadata = record.call_args.kwargs["metadata"]
        self.assertEqual(metadata["file_format"], "csv")
        self.assertEqual(metadata["count"], 1)
        self.assertEqual(metadata["lead_ids"], ["lead-id"])

    def test_note_update_records_previous_content(self):
        view = lead_views.LeadNoteUpdateView()
        note = SimpleNamespace(id="note-id", lead_id="lead-id", lead=SimpleNamespace(chatbot=self.chatbot))
        view.get_lead_note = Mock(return_value=note)
        serializer = Mock(validated_data={"content": "New note"})
        serializer.to_representation.side_effect = [{"content": "Old note"}, {"content": "New note"}]
        serializer.save.return_value = note
        view.get_serializer = Mock(return_value=serializer)
        with patch.object(lead_views, "record_chatbot_activity") as record:
            view.patch(self.request)
        self.assertEqual(record.call_args.kwargs["metadata"]["changes"], {
            "content": {"previous_value": "Old note", "updated_value": "New note"},
        })
