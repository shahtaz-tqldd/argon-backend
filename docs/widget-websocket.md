# Widget WebSocket

This is the frontend contract for the public embedded chat widget. Each socket
is scoped to one visitor conversation and must not use the dashboard access JWT.

The socket implementation lives in `base/socket`; HTTP bootstrap and
conversation logic live in `chat` and `chatbot`.

## Bootstrap before connecting

```http
POST /api/v1/chatbots/{public_key}/conversations/
Content-Type: application/json
Origin: https://customer.example
```

For a new conversation, send available visitor/page context:

```json
{
  "user_metadata": {"locale": "en-US"},
  "metadata": {"page_url": "https://customer.example/pricing"}
}
```

To resume, send the previously stored token:

```json
{"conversation_token": "signed-token"}
```

The response contains `data.session`, the latest 50 messages in chronological
order, a refreshed `data.conversation_token`, and `data.websocket_url`.
Always use the returned WebSocket URL instead of constructing it.

```json
{
  "data": {
    "session": {
      "id": "session-uuid",
      "visitor_id": "server-generated-id",
      "status": "open",
      "ai_enabled": true
    },
    "conversation_token": "signed-token",
    "websocket_url": "wss://api.example.com/ws/widget/chatbots/public-key/conversations/session-uuid/?token=signed-token",
    "resumed": false,
    "messages": []
  }
}
```

Store the token according to the widget's privacy policy. It is bound to the
conversation, chatbot, and visitor and expires after 30 days by default. A
resolved or closed session is not resumable; bootstrap creates a new open one.

## Connect

The returned URL has this form:

```text
wss://{api-host}/ws/widget/chatbots/{public_key}/conversations/{session_id}/?token={conversation-token}
```

Use `wss://` in production and do not log its query string. When allowed
origins exist for the chatbot, the browser's `Origin` must exactly match an
active allowed origin.

| Close code | Meaning | Recovery |
| --- | --- | --- |
| `4401` | Token missing, invalid, or expired | Bootstrap with the stored token; if HTTP returns `401`, bootstrap without it |
| `4403` | Origin denied or token/chatbot/session mismatch | Stop retrying; correct the origin or bootstrap data |
| `4404` | Chatbot or open conversation not found | Bootstrap again; a new session may be returned |

After acceptance:

```json
{
  "type": "connection.ready",
  "session_id": "session-uuid",
  "data": {"status": "open", "ai_enabled": true}
}
```

Only show the widget as live after this event.

## Send a visitor message

```json
{
  "type": "message.send",
  "client_message_id": "client-generated-uuid",
  "content": "Do you offer refunds?",
  "metadata": {"page_url": "https://customer.example/pricing"}
}
```

- `content` must be non-blank and at most 10,000 characters.
- `metadata` is optional and must be an object.
- `client_message_id` is optional, must be at most 255 characters, and should
  be a new UUID for each logical message.
- Reuse the same `client_message_id` when retrying. This makes sending
  idempotent within the conversation.

The server acknowledges persistence with:

```json
{
  "type": "message.accepted",
  "session_id": "session-uuid",
  "data": {
    "message_id": "message-uuid",
    "client_message_id": "client-generated-uuid",
    "duplicate": false
  }
}
```

The persisted message also arrives as `message.created`. Reconcile an
optimistic message using `client_message_id`/`external_id`, then upsert by
the server message ID. `duplicate: true` confirms a retry found the existing
message and does not queue another AI reply.

The REST message endpoint is an alternative transport. Do not send through both
REST and WebSocket unless both attempts reuse the same `client_message_id`.

## Receive messages

```json
{
  "type": "message.created",
  "session_id": "session-uuid",
  "data": {
    "id": "message-uuid",
    "chat_session_id": "session-uuid",
    "sender_type": "ai",
    "sender": null,
    "content": "Yes. Refunds are available within 30 days.",
    "status": "sent",
    "external_id": "ai:visitor-message-uuid",
    "metadata": {"in_reply_to": "visitor-message-uuid"},
    "attachments": [],
    "created_at": "2026-09-09T10:00:00+00:00",
    "updated_at": "2026-09-09T10:00:00+00:00"
  }
}
```

`sender_type` is `visitor`, `ai`, `agent`, or `system`. For agent
messages, `sender` is deliberately limited to public fields:

```json
{"name": "Support Agent", "avatar": "https://example.com/avatar.webp"}
```

The widget never receives the agent's email, user ID, or chatbot membership ID.
Upsert by `data.id` and order by `created_at`, then `id`. AI output is not
token-streamed; the complete reply arrives in one `message.created` event.

## AI lifecycle

```json
{
  "type": "ai.response.started",
  "session_id": "session-uuid",
  "data": {"in_reply_to": "visitor-message-uuid"}
}
```

Use this to show a typing/loading state. Clear it when the reply's
`message.created` arrives or on failure:

```json
{
  "type": "ai.response.failed",
  "session_id": "session-uuid",
  "data": {"code": "generation_failed", "retryable": true}
}
```

| Failure code | Meaning |
| --- | --- |
| `queue_unavailable` | The reply could not be queued |
| `message_limit_reached` | The chatbot's AI message capacity is exhausted |
| `generation_failed` | Reply generation failed after retries |

Respect `retryable` when deciding whether to offer retry UI.

## Session state events

The widget receives only visitor-safe state:

| Event | Widget `data` | Suggested behavior |
| --- | --- | --- |
| `session.taken_over` | `{"ai_enabled":false}` | A human agent now owns the conversation |
| `session.transferred` | `{"ai_enabled":false}` | Human ownership changed; identities stay hidden |
| `session.released` | `{"ai_enabled":true}` | AI may respond again |
| `session.reopened` | `{"ai_enabled":true}` | The conversation is open again |
| `session.resolved` | `{"ai_enabled":false,"status":"resolved"}` | Disable composer and offer a new-conversation flow |
| `session.closed` | `{"ai_enabled":false,"status":"closed"}` | Disable composer and offer a new-conversation flow |

Internal `session.transfer_requested`, `session.transfer_declined`, and
`session.transfer_cancelled` events are not delivered. `session.updated` is
dashboard-only. Do not depend on internal takeover IDs or agent IDs in widget
code.

## Errors and keepalive

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

Command error codes are `unsupported_event`, `invalid_content`,
`invalid_metadata`, `invalid_client_message_id`, and `message_rejected`.

During idle periods send `{"type":"ping"}`; the response is
`{"type":"pong"}`. The widget has no presence protocol and receives no
dashboard presence events.

## Reconnection and consistency

WebSocket delivery is transient. On an abnormal disconnect:

1. Reconnect to the same URL with exponential backoff and jitter while the token
   and conversation remain valid.
2. For `4401` or `4404`, call bootstrap to rotate/validate the token and
   recover current messages or a new session.
3. Reconcile history with bootstrap's latest 50 messages and upsert by ID.
4. Keep pending outbound messages by `client_message_id`; retry an
   unacknowledged send with that same ID.
5. Stop retrying when the host destroys the widget or goes offline, and resume
   only when appropriate.

## Minimal browser implementation

```ts
export function openWidgetSocket(
  websocketUrl: string,
  onEvent: (event: Record<string, unknown>) => void,
) {
  const socket = new WebSocket(websocketUrl);

  socket.addEventListener("message", ({ data }) => {
    try {
      onEvent(JSON.parse(String(data)));
    } catch {
      // Report malformed frames without crashing the host page.
    }
  });

  return {
    socket,
    sendMessage(content: string, clientMessageId: string) {
      if (socket.readyState !== WebSocket.OPEN) return false;
      socket.send(JSON.stringify({
        type: "message.send",
        client_message_id: clientMessageId,
        content,
        metadata: {},
      }));
      return true;
    },
  };
}
```

For the full HTTP bootstrap and REST fallback contract, see
[`chat/WIDGET_API.md`](../chat/WIDGET_API.md).
