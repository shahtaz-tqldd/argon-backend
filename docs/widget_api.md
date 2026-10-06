# Chatbot widget conversation API

The widget uses the chatbot widget `public_key` for discovery and a signed
`conversation_token` for access to one visitor conversation. Do not use a
chatbot UUID, slug, or visitor ID as authentication.

## 1. Load public chatbot configuration

```http
GET /api/v1/chatbots/{public_key}/config/
```

This request is public. It returns the public chatbot identity and renderable
widget settings. A missing, deleted, disabled, or widget-disabled chatbot
returns `404`.

## 2. Create a visitor and start a session

```http
POST /api/v1/chatbots/{public_key}/visitor/create/?visitor_id={visitor_id}
Content-Type: application/json
Origin: https://customer.example
```

For an anonymous visitor, the body may be empty or may contain widget context:

```json
{
  "user_metadata": {"locale": "en-US"},
  "detected_location": "Dhaka, BD",
  "detected_country": "BD",
  "metadata": {"page_url": "https://customer.example/pricing"}
}
```

`user_metadata`, `detected_location`, and `detected_country` enrich a
server-side visitor profile keyed by `visitor_id`; the IP address is captured
from the request itself. If an open session already exists for the anonymous
visitor, the endpoint returns
that session with HTTP `200`. Otherwise it creates a visitor session and returns
HTTP `201`.

When the visitor submits the configured lead form, include `lead_data`. The lead
fields are validated against the enabled lead-capture configuration and a new
session is initiated:

```json
{
  "lead_data": {
    "name": "Ada Lovelace",
    "email": "ada@example.com"
  },
  "user_metadata": {"locale": "en-US"}
}
```

The response contains `visitor`, `session`, `conversation_token`,
`websocket_url`, `visitor_created`, and `session_created`. Store the conversation
token for authenticated message and appointment requests.

### Get visitor details

```http
GET /api/v1/chatbots/{public_key}/visitor/details/?visitor_id={visitor_id}
Origin: https://customer.example
```

The response contains `visitor_id`, `lead_id`, `lead_data`, `ip_address`,
`detected_location`, `detected_country`, and `user_metadata`.

If the chatbot has allowed-origin records, visitor create/details requests and
the WebSocket handshake require an exact active origin match.

## 3. Connect the visitor WebSocket

For the standalone frontend socket contract, lifecycle guidance, event matrix,
and reconnection strategy, see [`docs/widget-websocket.md`](../docs/widget-websocket.md).

Use the exact `websocket_url` returned by the bootstrap API:

```text
wss://{api-host}/ws/widget/chatbots/{public_key}/conversations/{session_id}/?token={conversation_token}
```

Browser WebSocket clients cannot set an `Authorization` header, so the signed
conversation token is passed in the query string. Use `wss` in production and
avoid logging query strings at the proxy/load-balancer layer.

After the connection is accepted, the server sends:

```json
{
  "type": "connection.ready",
  "session_id": "51a3d974-a8ae-4ca4-956b-8d493ad97fe8",
  "data": {
    "status": "open",
    "ai_enabled": true
  }
}
```

Handshake close codes:

| Code | Meaning |
| --- | --- |
| `4401` | Missing, invalid, or expired conversation token |
| `4403` | Token/chatbot/session mismatch or disallowed origin |
| `4404` | Chatbot or resumable conversation no longer exists |

## 4. Send a visitor message

The canonical send API is:

```http
POST /api/v1/chatbots/{public_key}/messages/create/?visitor_id={visitor_id}&session_id={session_id}
Authorization: Bearer {conversation_token}
Content-Type: application/json
Origin: https://customer.example
```

```json
{
  "client_message_id": "8fc35331-daf9-4f8a-8538-f021b2ac38df",
  "content": "Do you offer refunds?",
  "metadata": {
    "page_url": "https://customer.example/pricing"
  }
}
```

`client_message_id` is optional but strongly recommended. Generate one UUID per
outgoing message and reuse it when retrying; this makes sends idempotent. Content
must be non-blank and at most 10,000 characters. Metadata must be a JSON object.

Creation returns `201`; an idempotent retry returns `200` with
`duplicate: true`:

```json
{
  "status": 201,
  "success": true,
  "message": "Message accepted successfully.",
  "data": {
    "message": {
      "id": "5cf9bbab-64a9-44c2-8666-37e07eb7109a",
      "sender_type": "visitor",
      "content": "Do you offer refunds?",
      "external_id": "8fc35331-daf9-4f8a-8538-f021b2ac38df"
    },
    "duplicate": false,
    "ai_queued": true
  }
}
```

The visitor WebSocket also accepts the equivalent event when an all-socket
transport is preferred:

```json
{
  "type": "message.send",
  "client_message_id": "8fc35331-daf9-4f8a-8538-f021b2ac38df",
  "content": "Do you offer refunds?",
  "metadata": {"page_url": "https://customer.example/pricing"}
}
```

It responds with `message.accepted`, whose `data` contains `message_id`,
`client_message_id`, and `duplicate`. Use either REST or WebSocket for a send,
not both, unless the same `client_message_id` is reused.

## 5. Receive AI and agent responses

All persisted messages—including the visitor's own message, AI replies, and
human-agent replies—are delivered as `message.created`:

```json
{
  "type": "message.created",
  "session_id": "51a3d974-a8ae-4ca4-956b-8d493ad97fe8",
  "data": {
    "id": "message-uuid",
    "chat_session_id": "51a3d974-a8ae-4ca4-956b-8d493ad97fe8",
    "sender_type": "ai",
    "sender": null,
    "content": "Yes. Refunds are available within 30 days.",
    "status": "sent",
    "external_id": "ai:visitor-message-uuid",
    "metadata": {
      "model": "gemini-2.5-flash",
      "in_reply_to": "visitor-message-uuid",
      "knowledge_sources": [],
      "usage": {
        "input_tokens": 120,
        "output_tokens": 18,
        "total_tokens": 138
      }
    },
    "attachments": [],
    "created_at": "2026-09-01T12:00:00+00:00",
    "updated_at": "2026-09-01T12:00:00+00:00"
  }
}
```

`sender_type` is one of `visitor`, `ai`, `agent`, or `system`. For an agent
message, `sender` only contains public `name` and `avatar` fields.

AI lifecycle events:

```json
{
  "type": "ai.response.started",
  "session_id": "session-uuid",
  "data": {"in_reply_to": "visitor-message-uuid"}
}
```

```json
{
  "type": "ai.response.failed",
  "session_id": "session-uuid",
  "data": {
    "code": "generation_failed",
    "retryable": true
  }
}
```

Failure codes include `generation_failed`, `queue_unavailable`, and
`message_limit_reached`. Validation failures use an `error` event:

```json
{
  "type": "error",
  "session_id": "session-uuid",
  "data": {
    "code": "invalid_content",
    "message": "Message content cannot be blank."
  }
}
```

AI output is currently **not token-streamed**. The complete reply arrives in one
`message.created` event. Human agent messages use the same event. A takeover
turns AI off for that session, preventing AI and an agent from replying at the
same time.

`gemini` when model-backed replies are ready; Gemini work is queued in Celery.

The socket also emits `session.taken_over`, `session.transferred`,
`session.released`, `session.reopened`, `session.resolved`, or `session.closed`.
Their public payload contains `ai_enabled` and relevant session `status`; a
resolution keeps the conversation open and returns it to AI, while a close
ends it. Internal agent and takeover identifiers are not exposed.

Send `{"type":"ping"}` during idle periods; the server responds with
`{"type":"pong"}`. On disconnect, reconnect with the same URL while the token
is valid. If the server closes with `4401`, call the bootstrap endpoint with the
stored token to rotate/validate it; if that returns `401`, start without a token.

## Deployment requirements

- Run the ASGI application (`app.asgi:application`), not WSGI-only deployment.
- Run Redis for the Channels layer and Celery broker.
- Run a Celery worker so `chat.tasks.generate_ai_reply_task` executes.
- Configure `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, and Google ADC.
- Configure `GEMINI_CHAT_MODEL` if overriding `gemini-2.5-flash`.
- Use `CHATBOT_AI_BACKEND=placeholder` for connection testing, or `gemini` for
  model-backed replies.
- Set `WIDGET_WEBSOCKET_BASE_URL` when WebSockets use a different public host
  or port from the HTTP API. Development Compose defaults it to
  `ws://localhost:8008`; production should use the public `wss://` endpoint or
  route `/ws/` to ASGI on the same public hostname.
- Configure the chatbot's allowed-origin list when the widget must be restricted
  to specific customer sites. The public widget routes are dynamically enabled
  for CORS; other APIs still use the global CORS allowlist.
