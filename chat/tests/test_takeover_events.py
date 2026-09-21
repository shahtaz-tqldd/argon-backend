from types import SimpleNamespace
from uuid import uuid4

from django.test import SimpleTestCase

from chat.services.takeover import _session_management_state


class SessionManagementEventStateTests(SimpleTestCase):
    @staticmethod
    def agent(name):
        return SimpleNamespace(id=uuid4(), user=SimpleNamespace(name=name))

    def test_assignment_state_contains_frontend_patch(self):
        owner = self.agent("Current Agent")
        recipient = self.agent("Next Agent")
        session = SimpleNamespace(
            status="open",
            ai_enabled=False,
            assigned_to=owner,
        )
        transfer = SimpleNamespace(to_agent=recipient)

        state = _session_management_state(
            session,
            pending_transfer=transfer,
        )

        self.assertEqual(
            state,
            {
                "status": "open",
                "ai_enabled": False,
                "assigned_to": {
                    "id": str(owner.id),
                    "name": "Current Agent",
                },
                "has_pending_transfer": True,
                "transfer_requested_to": {
                    "id": str(recipient.id),
                    "name": "Next Agent",
                },
            },
        )

    def test_unassigned_state_explicitly_clears_frontend_fields(self):
        session = SimpleNamespace(
            status="resolved",
            ai_enabled=False,
            assigned_to=None,
        )

        self.assertEqual(
            _session_management_state(session),
            {
                "status": "resolved",
                "ai_enabled": False,
                "assigned_to": None,
                "has_pending_transfer": False,
                "transfer_requested_to": None,
            },
        )
