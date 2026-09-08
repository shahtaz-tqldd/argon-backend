"""Public event names. New domain events use the same type/data envelope."""
NOTIFICATION_CREATED = "notification.created"
MEMBER_ONLINE = "member.online"
MEMBER_OFFLINE = "member.offline"
PRESENCE_SNAPSHOT = "presence.snapshot"
SESSION_UPDATED = "session.updated"

# Existing specific transitions remain available alongside session.updated.
SESSION_TRANSITIONS = frozenset({
    "session.taken_over", "session.released", "session.resolved",
    "session.closed", "session.reopened", "session.transfer_requested",
    "session.transferred", "session.transfer_declined", "session.transfer_cancelled",
})

# Documentation/discovery registry, not a delivery whitelist: the broadcaster can
# deliver future domain events without adding another consumer or connection.
DASHBOARD_EVENT_TYPES = SESSION_TRANSITIONS | frozenset({
    NOTIFICATION_CREATED, MEMBER_ONLINE, MEMBER_OFFLINE, PRESENCE_SNAPSHOT,
    SESSION_UPDATED, "connection.ready", "message.created", "session.created",
    "ai.response.started", "ai.response.failed", "session.subscribed",
    "session.unsubscribed", "message.accepted", "pong", "error",
})
