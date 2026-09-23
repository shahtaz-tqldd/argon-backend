# Dashboard client WebSocket

This is the frontend contract for the authenticated Argon dashboard. Use one
WebSocket per signed-in browser tab for notifications, conversations, agent
commands, and workspace presence.

The implementation lives in `base/socket`. REST remains the source of truth;
WebSocket events are transient updates and can be missed while disconnected.

## Connect

```text
wss://{api-host}/ws/dashboard/?token={access-token}
```

- Use the same access JWT as the authenticated REST API.
- Browser `WebSocket` cannot set an `Authorization` header, so browsers must
  use the `token` query parameter. Non-browser clients may instead send
  `Authorization: Bearer {access-token}`.
- Use `wss://` outside local development and redact query strings from logs.
- A missing/invalid token or inactive user is rejected with close code `4401`.

After acceptance:

```json
{
  "type": "connection.ready",
  "data": {
    "heartbeat_interval_seconds": 25,
    "presence_timeout_seconds": 75
  }
}
```

Treat these values as authoritative rather than hard-coding them. The server
then sends one `presence.snapshot` for every workspace and chatbot the user
can access.

## Connection lifecycle

1. Open the socket after obtaining an access token.
2. Wait for `connection.ready` before treating it as usable.
3. Send `{"type":"presence.heartbeat"}` at the supplied interval.
4. Stop the heartbeat timer when the socket closes.
5. Reconnect with exponential backoff and jitter, refreshing the access token
   first when necessary.
6. After reconnecting, refresh REST state and restore active
   `session.subscribe` subscriptions. Automatic workspace/chatbot subscriptions
   require no client action.

Close code `1013` means presence storage is temporarily unavailable; retry with
backoff. Retry other abnormal/network closes too, but not after sign-out.

## Client commands

Every command is a JSON object with a string `type`.

### Heartbeat

```json
{"type": "presence.heartbeat"}
```

`ping` is an alias. A successful heartbeat produces fresh presence snapshots
followed by `{"type":"pong"}`.

### Session-specific notification subscription

```json
{"type": "session.subscribe", "session_id": "session-uuid"}
```

The acknowledgement is:

```json
{"type": "session.subscribed", "session_id": "session-uuid"}
```

Chat events for authorized chatbots already arrive automatically. This command
only adds notifications specifically addressed to that session. It is
idempotent, access-controlled, and limited to 100 distinct sessions per socket.

Unsubscribe with:

```json
{"type": "session.unsubscribe", "session_id": "session-uuid"}
```

The response is `session.unsubscribed` with the same top-level `session_id`.
Subscriptions last only for the socket lifetime and must be restored after a
reconnect.

### Send an agent message

```json
{
  "type": "message.send",
  "session_id": "session-uuid",
  "content": "Hello! How can I help?",
  "metadata": {}
}
```

`content` must be non-blank and at most 10,000 characters. `metadata` is
optional and must be an object. The user needs current chat-session management
permission and must be the active takeover agent.

```json
{
  "type": "message.accepted",
  "session_id": "session-uuid",
  "message_id": "message-uuid"
}
```

Acceptance means the message was persisted. It is also broadcast as
`message.created`; upsert it by `data.id`. Dashboard socket sends are not
idempotent, so do not automatically resend an unacknowledged command. Refetch
messages after reconnecting to determine the final state.

## Server events

### Message created

```json
{
  "type": "message.created",
  "session_id": "session-uuid",
  "data": {
    "id": "message-uuid",
    "chat_session_id": "session-uuid",
    "sender_type": "agent",
    "sender": {
      "id": "chatbot-membership-uuid",
      "user_id": "user-uuid",
      "name": "Support Agent",
      "email": "agent@example.com",
      "avatar": "https://example.com/avatar.webp"
    },
    "content": "Hello! How can I help?",
    "status": "sent",
    "external_id": "",
    "metadata": {},
    "attachments": [],
    "created_at": "2026-09-09T10:00:00+00:00",
    "updated_at": "2026-09-09T10:00:00+00:00"
  }
}
```

`sender_type` is `visitor`, `ai`, `agent`, or `system`. `sender` is
populated for an agent and otherwise is `null`. Compare `sender.user_id` with
the signed-in user ID to identify the current agent. Order by `created_at`,
then `id`, and upsert by `id`.

### Session and AI events

These events share `{"type", "session_id", "data"}`:

| Event | `data` fields |
| --- | --- |
| `session.created` | `chatbot_id`, `channel`, `status` |
| `session.taken_over` | `takeover_id`, `agent_id`, `is_forced`, `takeover_reason` |
| `session.released` | `takeover_id`; forced returns also include `agent_id`, `is_forced`, and `note` |
| `session.resolved` | `takeover_id`, `status` (`open`) |
| `session.closed` | `takeover_id`, `status` (`closed`) |
| `session.reopened` | `reopened_by_id` |
| `session.transfer_requested` | `transfer_id`, `transfer_status`, `takeover_id`, `from_agent_id`, `to_agent_id` |
| `session.transferred` | `transfer_id`, `transfer_status`, `takeover_id`, `from_agent_id`, `to_agent_id` |
| `session.transfer_declined` / `session.transfer_cancelled` | `transfer_id`, `transfer_status` |
| `ai.response.started` | `in_reply_to` message UUID |
| `ai.response.failed` | `code`, `retryable` |

Every management transition also includes `status`, `ai_enabled`,
`assigned_to`, `has_pending_transfer`, and `transfer_requested_to`. It produces
this canonical dashboard-only event for every authorized member of the
chatbot:

```json
{
  "type": "session.updated",
  "session_id": "session-uuid",
  "data": {
    "change": "session.taken_over",
    "takeover_id": "takeover-uuid",
    "agent_id": "chatbot-membership-uuid",
    "status": "open",
    "ai_enabled": false,
    "assigned_to": {
      "id": "chatbot-membership-uuid",
      "name": "Support Agent"
    },
    "has_pending_transfer": false,
    "transfer_requested_to": null
  }
}
```

Choose either the specific transition or `session.updated` for state changes;
handling both applies the same change twice. New frontend code should use
`session.updated`. See [Frontend: real-time chat-session management](frontend-chat-session-realtime.md)
for the complete implementation contract.

Known AI failure codes are `queue_unavailable`, `message_limit_reached`, and
`generation_failed`. AI replies are not streamed; the complete reply arrives
as `message.created`.

### Notifications

```json
{
  "type": "notification.created",
  "data": {
    "id": "notification-uuid",
    "recipient_type": "chatbot",
    "notification_type": "training_complete",
    "event": "training_complete",
    "title": "Knowledge training complete",
    "message": "",
    "metadata": {},
    "workspace_id": null,
    "chatbot_id": "chatbot-uuid",
    "target_id": null,
    "is_read": false,
    "read_at": null,
    "created_at": "2026-09-09T10:00:00+00:00"
  }
}
```

Route by top-level `type`. Inside a notification, `data.event` equals
`data.notification_type` and is retained for compatibility. Persistent/read
state comes from REST.

### Presence

Use chatbot presence beside the chatbot member-list API:

```json
{
  "type": "presence.snapshot",
  "data": {
    "chatbot_id": "chatbot-uuid",
    "member_ids": ["chatbot-membership-uuid"],
    "version": 1788948000000000
  }
}
```

Each `member_id` is the top-level `id` returned by the chatbot member-list
API. Filter the stored member list by those IDs to render full details.
Incremental changes use `member.online` and `member.offline`:

```json
{
  "type": "member.online",
  "data": {
    "chatbot_id": "chatbot-uuid",
    "member_id": "chatbot-membership-uuid",
    "user_id": "user-uuid",
    "version": 1788948000000001
  }
}
```

`member.offline` has the same identifying fields but may omit `user_id`. Use
`member_id` for member-list matching.

Workspace presence remains available for workspace-wide UI:

```json
{
  "type": "presence.snapshot",
  "data": {
    "workspace_id": "workspace-uuid",
    "user_ids": ["user-uuid"],
    "version": 1788948000000000
  }
}
```

Workspace `member.online` and `member.offline` contain `workspace_id`,
`user_id`, and `version`. Versions are Redis server timestamps in microseconds.
Because events can be reordered across workers:

- Keep the latest snapshot version per workspace and latest transition version
  per user.
- Ignore a transition at or below the applicable stored version.
- When applying a newer snapshot, preserve per-user transitions newer than that
  snapshot.
- Clear presence state when disconnected or workspace access is removed.

Apply the same version rules per chatbot/member for chatbot-scoped events.

Disconnect does not immediately make a user offline because another tab/device
may be active. Offline normally appears 75–90 seconds after the last heartbeat.

### Errors

Dashboard command errors use:

```json
{"type": "error", "message": "Session is unavailable or access is denied."}
```

Errors have no request ID or machine-readable code. Serialize commands if they
must be associated with errors, or present errors as general socket failures.

## Authorization

The server automatically attaches the connection to permitted global, user,
workspace, and chatbot audiences. Clients cannot name groups. Active workspace
membership is required for workspace data. Active chatbot membership and
`CHAT_SESSION_MANAGEMENT` permission are required for chat delivery/commands.
Access is checked again during commands and event delivery.

New access is discovered on the next command/heartbeat. After membership changes,
reconnect and refetch REST state for predictable resynchronization.

## Minimal browser implementation

```ts
export function openDashboardSocket(
  apiWsBaseUrl: string,
  accessToken: string,
  onEvent: (event: Record<string, unknown>) => void,
) {
  const url = new URL("/ws/dashboard/", apiWsBaseUrl);
  url.searchParams.set("token", accessToken);

  const socket = new WebSocket(url);
  let heartbeat: ReturnType<typeof setInterval> | undefined;

  socket.addEventListener("message", ({ data }) => {
    const event = JSON.parse(String(data));
    if (event.type === "connection.ready") {
      const seconds = event.data.heartbeat_interval_seconds;
      heartbeat = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "presence.heartbeat" }));
        }
      }, seconds * 1000);
    }
    onEvent(event);
  });

  socket.addEventListener("close", () => {
    if (heartbeat) clearInterval(heartbeat);
    // Reconnect with backoff unless the user signed out.
  });

  return socket;
}
```

A minimal chatbot-member reducer can keep a `Set` of online membership IDs:

```ts
function applyChatbotPresence(
  event: any,
  chatbotId: string,
  onlineMemberIds: Set<string>,
) {
  if (event.data?.chatbot_id !== chatbotId) return onlineMemberIds;

  if (event.type === "presence.snapshot") {
    return new Set<string>(event.data.member_ids);
  }

  const next = new Set(onlineMemberIds);
  if (event.type === "member.online") next.add(event.data.member_id);
  if (event.type === "member.offline") next.delete(event.data.member_id);
  return next;
}

const onlineMembers = members.filter(member => onlineMemberIds.has(member.id));
```

Production clients should guard JSON parsing, cap and jitter reconnect delays,
refetch REST state after reconnect, and restore session-specific subscriptions.
