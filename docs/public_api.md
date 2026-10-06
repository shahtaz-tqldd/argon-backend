# Public Widget API

This document describes the visitor-facing HTTP endpoints used by the embedded
chat widget.

## Base URL

```text
/api/v1/chatbots/{public_key}/
```

`public_key` is the public widget key configured for the chatbot.

## Common behavior

- These endpoints do not use the dashboard JWT.
- Send the browser `Origin` header. When the chatbot has configured allowed
  origins, the origin must match an active entry.
- Conversation-protected operations use:

```http
Authorization: Bearer {conversation_token}
```

- JSON requests must send `Content-Type: application/json`.
- IDs returned by the API are UUID strings unless stated otherwise.
- Timestamps are ISO 8601 strings.

### Success envelope

```json
{
  "status": 200,
  "success": true,
  "message": "Operation completed successfully.",
  "data": {}
}
```

Paginated responses also contain a top-level `meta` object.

### Error envelope

```json
{
  "status": 400,
  "success": false,
  "message": "Request validation failed.",
  "errors": {
    "field_name": ["Error description."]
  }
}
```

Common status codes:

| Status | Meaning |
| --- | --- |
| `200` | Successful lookup, duplicate/idempotent operation, or existing resource |
| `201` | Resource or session created |
| `400` | Missing or invalid query/body data |
| `401` | Missing, invalid, or expired conversation token |
| `403` | Origin is not allowed or token/resource ownership does not match |
| `404` | Chatbot, visitor, session, or another resource was not found |
| `409` | The requested state change conflicts with current data |

---

## 1. Chatbot configuration

Returns the public configuration required to render the widget.

### Endpoint

```http
GET /api/v1/chatbots/{public_key}/config/
```

### Query parameters

None.

### Payload

None.

### Expected response

`200 OK`

```json
{
  "status": 200,
  "success": true,
  "message": "Chatbot widget configuration fetched successfully.",
  "data": {
    "chatbot_name": "Support Bot",
    "logo": "https://cdn.example.com/chatbot-logo.png",
    "language": "en",
    "welcome_message": "How can I help?",
    "widget_settings": {
      "primary_color": "#111827",
      "secondary_color": "#FFFFFF",
      "launcher_position": "right",
      "launcher_text": "Chat with us",
      "header_title": "Support",
      "header_description": "We usually reply instantly.",
      "show_branding": true,
      "theme": "light",
      "other_settings": {}
    },
    "lead_config": {
      "is_enabled": true,
      "collectable_fields": [
        {
          "label": "Name",
          "value": "name",
          "mode": "required",
          "type": "text"
        },
        {
          "label": "Email",
          "value": "email",
          "mode": "required",
          "type": "email"
        }
      ],
      "auto_collect": true,
      "require_consent": false,
      "consent_message": ""
    },
    "appointment_config": {
      "collectable_fields": [],
      "confirmation_message": "Your appointment request was received."
    }
  }
}
```

`lead_config` and `appointment_config` are `null` when their corresponding
features are disabled.

---

## 2. Create or initialize a visitor

Creates the visitor session used to start chatting.

When `lead_data` is omitted, an existing open session for the same visitor is
reused. If no open session exists, a new session is created.

When `lead_data` is supplied, the data is validated against the enabled lead
capture configuration. A lead is created or updated and a new chat session is
always initiated.

### Endpoint

```http
POST /api/v1/chatbots/{public_key}/visitor/create/?visitor_id={visitor_id}
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `visitor_id` | string, maximum 255 characters | Yes | Stable browser/device visitor identifier |

### Payload

All body fields are optional.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `lead_data` | object | No | Values submitted through the configured lead form |
| `user_metadata` | object | No | Visitor/browser metadata such as locale or timezone; merged into the visitor profile |
| `detected_location` | string, maximum 255 characters | No | Detected location such as "Dhaka, BD" |
| `detected_country` | string, maximum 64 characters | No | Detected country name or ISO code |
| `metadata` | object | No | Session context such as the current page URL |

The visitor's IP address is captured from the request itself. Identity fields
live on a server-side `ChatbotVisitor` record keyed by `visitor_id`; every
request enriches that single profile (new metadata keys override old ones,
existing keys are preserved).

Anonymous visitor example:

```json
{
  "user_metadata": {
    "locale": "en-US",
    "timezone": "Asia/Dhaka"
  },
  "detected_location": "Dhaka, BD",
  "detected_country": "BD",
  "metadata": {
    "page_url": "https://customer.example/pricing"
  }
}
```

Lead-form example:

```json
{
  "lead_data": {
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "phone": "+1-555-0100"
  },
  "user_metadata": {
    "locale": "en-US"
  },
  "metadata": {
    "page_url": "https://customer.example/contact"
  }
}
```

### Expected response

`201 Created` when a new session is created. `200 OK` when an existing open
anonymous session is reused.

```json
{
  "status": 201,
  "success": true,
  "message": "Visitor created successfully.",
  "data": {
    "visitor": {
      "visitor_id": "browser-visitor-123",
      "lead_id": "7bf5912a-737b-43d5-a0df-b55b45ac613a",
      "lead_data": {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": "+1-555-0100"
      },
      "ip_address": "203.0.113.10",
      "detected_location": "Dhaka, BD",
      "detected_country": "BD",
      "user_metadata": {
        "locale": "en-US"
      }
    },
    "session": {
      "id": "51a3d974-a8ae-4ca4-956b-8d493ad97fe8",
      "visitor_id": "browser-visitor-123",
      "status": "open",
      "ai_enabled": true
    },
    "conversation_token": "signed-conversation-token",
    "websocket_url": "wss://api.example.com/ws/widget/chatbots/public-key/conversations/51a3d974-a8ae-4ca4-956b-8d493ad97fe8/?token=signed-conversation-token",
    "visitor_created": true,
    "session_created": true
  }
}
```

Flag meanings:

| Field | Meaning |
| --- | --- |
| `visitor_created` | No `ChatbotVisitor` profile existed for this `visitor_id` yet |
| `session_created` | This request created a new session rather than reusing one |

A form submission fails with `400` when lead capture is disabled or the form
does not satisfy the configured required fields and field types.

---

## 3. Get visitor details

Returns the `ChatbotVisitor` profile: detected identity columns and the
metadata accumulated across sessions, plus the linked lead (if captured).

### Endpoint

```http
GET /api/v1/chatbots/{public_key}/visitor/details/?visitor_id={visitor_id}
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `visitor_id` | string, maximum 255 characters | Yes | Visitor identifier |

### Payload

None.

### Expected response

`200 OK`

```json
{
  "status": 200,
  "success": true,
  "message": "Visitor details fetched successfully.",
  "data": {
    "visitor_id": "browser-visitor-123",
    "lead_id": "7bf5912a-737b-43d5-a0df-b55b45ac613a",
    "lead_data": {
      "name": "Ada Lovelace",
      "email": "ada@example.com"
    },
    "ip_address": "203.0.113.10",
    "detected_location": "Dhaka, BD",
    "detected_country": "BD",
    "user_metadata": {
      "locale": "en-US"
    }
  }
}
```

For an anonymous visitor, `lead_id` is `null`, `lead_data` is an empty
object, and `ip_address` is `null` until the visitor has been seen with a
known address.

---

## 4. List visitor sessions

Returns all web-widget sessions belonging to the visitor. Sessions associated
through the same captured lead may also be included.

### Endpoint

```http
GET /api/v1/chatbots/{public_key}/sessions/list/?visitor_id={visitor_id}
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `visitor_id` | string, maximum 255 characters | Yes | Visitor identifier |

### Payload

None.

### Expected response

`200 OK`

```json
{
  "status": 200,
  "success": true,
  "message": "Visitor sessions fetched successfully.",
  "data": [
    {
      "id": "51a3d974-a8ae-4ca4-956b-8d493ad97fe8",
      "status": "open",
      "created_at": "2026-09-28T10:00:00Z",
      "last_activity_at": "2026-09-28T10:05:00Z",
      "conversation_token": "signed-conversation-token",
      "message_count": 3,
      "last_message": {
        "content": "How can I help?",
        "sender": "Support Bot",
        "created_at": "2026-09-28T10:05:00Z"
      }
    }
  ]
}
```

`last_message` is `null` when the session has no public messages. Internal
system messages are excluded from both `message_count` and `last_message`.

---

## 5. Create a new session

Creates another chat session for an existing visitor.

### Endpoint

```http
POST /api/v1/chatbots/{public_key}/sessions/create/?visitor_id={visitor_id}
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `visitor_id` | string, maximum 255 characters | Yes | Existing or new visitor identifier |

### Payload

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `user_metadata` | object | No | Visitor metadata merged into the visitor profile |
| `detected_location` | string, maximum 255 characters | No | Detected location such as "Dhaka, BD" |
| `detected_country` | string, maximum 64 characters | No | Detected country name or ISO code |
| `metadata` | object | No | Session context |

The visitor's IP address is captured from the request itself.

```json
{
  "user_metadata": {
    "locale": "en-US"
  },
  "detected_location": "Dhaka, BD",
  "detected_country": "BD",
  "metadata": {
    "page_url": "https://customer.example/help"
  }
}
```

### Expected response

`201 Created`

```json
{
  "status": 201,
  "success": true,
  "message": "Visitor session created successfully.",
  "data": {
    "session": {
      "id": "80658984-c640-452c-862d-234129a68356",
      "visitor_id": "browser-visitor-123",
      "status": "open",
      "ai_enabled": true
    },
    "conversation_token": "signed-conversation-token",
    "websocket_url": "wss://api.example.com/ws/widget/chatbots/public-key/conversations/80658984-c640-452c-862d-234129a68356/?token=signed-conversation-token"
  }
}
```

If the visitor already has a linked lead, the new session inherits it. When
`user_metadata` is omitted, metadata from an existing visitor session is
inherited.

---

## 6. List session messages

Returns public messages for one visitor session in chronological display order.

### Endpoint

```http
GET /api/v1/chatbots/{public_key}/messages/list/?visitor_id={visitor_id}&session_id={session_id}&conversation_token={conversation_token}
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `visitor_id` | string, maximum 255 characters | Yes | Visitor identifier |
| `session_id` | UUID | Yes | Session identifier |
| `conversation_token` | string | Yes | Signed token issued for the session |
| `page` | positive integer | No | Page number; defaults to the pagination default |
| `page_size` | positive integer | No | Requested page size, subject to server limits |

### Payload

None.

### Expected response

`200 OK`

```json
{
  "status": 200,
  "success": true,
  "message": "Conversation messages fetched successfully.",
  "meta": {
    "count": 2,
    "page": 1,
    "page_size": 20,
    "num_pages": 1,
    "next": null,
    "previous": null
  },
  "data": [
    {
      "id": "c700c753-89e7-4be4-a1ca-744819c05703",
      "chat_session_id": "51a3d974-a8ae-4ca4-956b-8d493ad97fe8",
      "sender_type": "visitor",
      "sender": null,
      "content": "Do you offer refunds?",
      "status": "sent",
      "external_id": "message-001",
      "metadata": {},
      "attachments": [],
      "created_at": "2026-09-28T10:01:00Z",
      "updated_at": "2026-09-28T10:01:00Z"
    },
    {
      "id": "9fd2de03-d690-42bd-b879-90e5e86a80b7",
      "chat_session_id": "51a3d974-a8ae-4ca4-956b-8d493ad97fe8",
      "sender_type": "agent",
      "sender": {
        "name": "Support Agent",
        "avatar": "https://cdn.example.com/avatar.png"
      },
      "content": "Yes, within 30 days.",
      "status": "sent",
      "external_id": "",
      "metadata": {},
      "attachments": [
        {
          "id": "8f617ff8-cbee-46ec-a4cd-eb98f899a048",
          "attachment_type": "document",
          "file_url": "https://cdn.example.com/refund-policy.pdf",
          "file_name": "refund-policy.pdf",
          "mime_type": "application/pdf",
          "file_size": 12000,
          "duration_ms": null,
          "sort_order": 0,
          "created_at": "2026-09-28T10:02:00Z"
        }
      ],
      "created_at": "2026-09-28T10:02:00Z",
      "updated_at": "2026-09-28T10:02:00Z"
    }
  ]
}
```

Messages marked with internal visibility are excluded.

---

## 7. Create a visitor message

Sends a visitor message to an open session. The AI response is queued
asynchronously when AI replies are enabled.

### Endpoint

```http
POST /api/v1/chatbots/{public_key}/messages/create/?visitor_id={visitor_id}&session_id={session_id}
Authorization: Bearer {conversation_token}
Content-Type: application/json
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `visitor_id` | string, maximum 255 characters | Yes | Visitor identifier |
| `session_id` | UUID | Yes | Open session identifier |

### Payload

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `content` | string, 1–10,000 characters | Yes | Non-blank visitor message |
| `client_message_id` | string, maximum 255 characters | No | Idempotency identifier generated by the client |
| `metadata` | object | No | Message metadata |

```json
{
  "content": "Do you offer refunds?",
  "client_message_id": "message-001",
  "metadata": {
    "page_url": "https://customer.example/pricing"
  }
}
```

### Expected response

`201 Created` for a new message. An idempotent retry using the same
`client_message_id` returns `200 OK` with `duplicate: true`.

```json
{
  "status": 201,
  "success": true,
  "message": "Message accepted successfully.",
  "data": {
    "message": {
      "id": "c700c753-89e7-4be4-a1ca-744819c05703",
      "chat_session_id": "51a3d974-a8ae-4ca4-956b-8d493ad97fe8",
      "sender_type": "visitor",
      "sender": null,
      "content": "Do you offer refunds?",
      "status": "sent",
      "external_id": "message-001",
      "metadata": {
        "page_url": "https://customer.example/pricing"
      },
      "attachments": [],
      "created_at": "2026-09-28T10:01:00Z",
      "updated_at": "2026-09-28T10:01:00Z"
    },
    "duplicate": false,
    "ai_queued": true
  }
}
```

The `visitor_id`, `session_id`, chatbot, and bearer token must all identify the
same conversation.

---

## 8. Book an appointment

Books an available appointment for the authenticated visitor session.

### Endpoint

```http
POST /api/v1/chatbots/{public_key}/book-appointment/?session_id={session_id}
Authorization: Bearer {conversation_token}
Content-Type: application/json
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `session_id` | UUID | Yes | Open visitor session identifier |

### Payload

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `starts_at` | ISO 8601 datetime with explicit UTC offset | Yes | Start time selected from available slots |
| `collected_fields` | object | No | Appointment form values |

```json
{
  "starts_at": "2026-10-01T10:00:00+00:00",
  "collected_fields": {
    "name": "Ada Lovelace",
    "email": "ada@example.com"
  }
}
```

### Expected response

`201 Created` for a new booking. Repeating an already accepted booking returns
`200 OK` with `duplicate: true`.

```json
{
  "status": 201,
  "success": true,
  "message": "Appointment request submitted successfully.",
  "data": {
    "appointment": {
      "id": "ac630690-54b2-4df6-ab32-46ec33fa950b",
      "collected_fields": {
        "name": "Ada Lovelace",
        "email": "ada@example.com"
      },
      "starts_at": "2026-10-01T10:00:00Z",
      "ends_at": "2026-10-01T10:30:00Z",
      "status": "pending",
      "created_at": "2026-09-28T10:10:00Z"
    },
    "duplicate": false,
    "agent_acknowledged": true,
    "agent_reply": "Your appointment request is awaiting approval."
  }
}
```

`agent_acknowledged` indicates whether the appointment confirmation was
successfully appended to the AI conversation. The booking can still succeed
when this flag is false.

---

## Chat transcript download

There is currently no visitor-facing transcript download endpoint under the
public API. Transcript export exists only in the authenticated dashboard API.
