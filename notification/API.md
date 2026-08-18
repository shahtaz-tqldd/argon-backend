# Notification API

Base path: `/api/v1/notifications/`

All REST endpoints require the regular authenticated bearer token:

```http
Authorization: Bearer <access_token>
```

## List Notifications

```http
GET /api/v1/notifications/
```

Returns paginated notifications for the authenticated user. By default this includes:

- general notifications for the user
- trip notifications for the user
- global notifications for all users

Query params:

| Param | Required | Description |
| --- | --- | --- |
| `page` | No | Page number. |
| `page_size` | No | Page size, max `100`. |
| `trip_id` | No | When provided, returns only trip notifications for that trip. The trip must belong to the authenticated user. |
| `type` | No | Filter by `general`, `trip`, or `global`. |
| `unread_only` | No | Use `true`, `1`, or `yes` to return only unread notifications. |

Dashboard example:

```http
GET /api/v1/notifications/?page=1&page_size=20
```

Trip page example:

```http
GET /api/v1/notifications/?trip_id=9c81f9fa-6ed1-4c98-8db0-7844033958d1
```

Response:

```json
{
  "status": 200,
  "success": true,
  "message": "Notifications fetched successfully.",
  "meta": {
    "count": 42,
    "page": 1,
    "page_size": 20,
    "num_pages": 3,
    "next": "http://localhost:8000/api/v1/notifications/?page=2",
    "previous": null,
    "unread_count": 7
  },
  "data": [
    {
      "id": "3af8f742-4ac4-4f65-912f-2c0c238d2928",
      "notification_type": "trip",
      "title": "Trip itinerary updated",
      "message": "Your itinerary has new recommendations.",
      "metadata": {
        "source": "planning_agent"
      },
      "trip_id": "9c81f9fa-6ed1-4c98-8db0-7844033958d1",
      "is_read": false,
      "read_at": null,
      "created_at": "2026-07-15T13:42:00Z"
    }
  ]
}
```

`meta.unread_count` follows the current list scope. If `trip_id` is sent, it is the unread count for that trip. If only `type` is sent, it is the unread count for that type. The `unread_only` param does not change the count.

## Mark One Notification Read

```http
PATCH /api/v1/notifications/<notification_id>/read/
```

Optional query params:

| Param | Required | Description |
| --- | --- | --- |
| `trip_id` | No | Restricts the operation to notifications from that trip scope. |

Response:

```json
{
  "status": 200,
  "success": true,
  "message": "Notification marked as read.",
  "data": {
    "id": "3af8f742-4ac4-4f65-912f-2c0c238d2928",
    "is_read": true,
    "read_at": "2026-07-15T14:10:00Z"
  }
}
```

## Mark All Notifications Read

```http
PATCH /api/v1/notifications/read-all/
```

Uses the same filters as the list endpoint.

Examples:

```http
PATCH /api/v1/notifications/read-all/
PATCH /api/v1/notifications/read-all/?trip_id=9c81f9fa-6ed1-4c98-8db0-7844033958d1
PATCH /api/v1/notifications/read-all/?type=global
```

Response:

```json
{
  "status": 200,
  "success": true,
  "message": "Notifications marked as read.",
  "data": {
    "marked_read_count": 7
  }
}
```

## Realtime WebSocket

Socket endpoint:

```text
ws://<host>/ws/notifications/?token=<access_token>
```

This receives all realtime notifications visible to the authenticated user:

- general notifications for the user
- trip notifications for the user
- global notifications for all users

Socket event payload:

```json
{
  "id": "3af8f742-4ac4-4f65-912f-2c0c238d2928",
  "notification_type": "trip",
  "title": "Trip itinerary updated",
  "message": "Your itinerary has new recommendations.",
  "metadata": {
    "source": "planning_agent"
  },
  "trip_id": "9c81f9fa-6ed1-4c98-8db0-7844033958d1",
  "is_read": false,
  "read_at": null,
  "created_at": "2026-07-15T13:42:00Z"
}
```

The dashboard can append every event it receives. A trip page can check whether `trip_id` matches the active trip before appending it to the trip notification list.

## Creating Notifications From Services

Use these helpers from any service, task, or view:

```python
from notification.services import (
    create_general_notification,
    create_global_notification,
    create_trip_notification,
)

create_general_notification(
    recipient=user,
    title="Welcome back",
    message="Your account is ready.",
    metadata={"source": "accounts"},
)

create_trip_notification(
    recipient=trip.user,
    trip=trip,
    title="Trip itinerary updated",
    message="Your itinerary has new recommendations.",
    metadata={"source": "planning_agent"},
)

create_global_notification(
    title="Scheduled maintenance",
    message="Tourtoise will be unavailable for maintenance tonight.",
    metadata={"severity": "info"},
)
```

Each helper stores the notification and emits the realtime socket event.

## Demo Notification Command

Create a global notification:

```bash
env/bin/python manage.py create_demo_notification \
  --type global \
  --title "Demo global notification" \
  --message "This notification goes to every dashboard socket."
```

Create a trip notification:

```bash
env/bin/python manage.py create_demo_notification \
  --type trip \
  --trip-id 9c81f9fa-6ed1-4c98-8db0-7844033958d1 \
  --title "Demo trip notification" \
  --message "This notification includes a trip_id in the socket payload."
```

Add custom metadata:

```bash
env/bin/python manage.py create_demo_notification \
  --type trip \
  --trip-id 9c81f9fa-6ed1-4c98-8db0-7844033958d1 \
  --metadata '{"source":"manual_demo","severity":"info"}'
```
