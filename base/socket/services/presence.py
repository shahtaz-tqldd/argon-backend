"""Ephemeral user/member presence. No disconnect mutation or database writes.

Redis TIME avoids worker clock skew. Lua serializes transitions against heartbeats.
Empty sorted sets disappear during the sweep, without a growing workspace registry.
"""
from functools import lru_cache

from django.conf import settings
from redis import Redis

from base.socket.events import (
    MEMBER_OFFLINE,
    MEMBER_ONLINE,
    PRESENCE_COUNT,
    PRESENCE_SNAPSHOT,
)
from base.socket.services.broadcaster import broadcast, publish_dashboard_event
from base.socket.services.groups import (
    chatbot_dashboard_group,
    chatbot_widget_group,
    workspace_dashboard_group,
)

_CLOCK = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000000 + tonumber(clock[2])
local cutoff = now - tonumber(ARGV[1]) * 1000000
"""
_HEARTBEAT = _CLOCK + """
local previous = redis.call('ZSCORE', KEYS[1], ARGV[2])
redis.call('ZADD', KEYS[1], now, ARGV[2])
local online = 0
if not previous or tonumber(previous) <= cutoff then online = 1 end
local users = redis.call('ZRANGEBYSCORE', KEYS[1], '(' .. cutoff, '+inf')
return {now, online, users}
"""
_EXPIRE = _CLOCK + """
local users = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', cutoff, 'LIMIT', 0, 500)
for _, user in ipairs(users) do redis.call('ZREM', KEYS[1], user) end
local online_count = redis.call('ZCOUNT', KEYS[1], '(' .. cutoff, '+inf')
return {now, online_count, users}
"""
_COUNT = _CLOCK + """
return redis.call('ZCOUNT', KEYS[1], '(' .. cutoff, '+inf')
"""


@lru_cache(maxsize=4)
def _client(url):
    return Redis.from_url(
        url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2,
    )


def get_client():
    return _client(settings.PRESENCE_REDIS_URL)


def workspace_key(workspace_id):
    return f"{settings.PRESENCE_REDIS_PREFIX}:workspace:{workspace_id}"


def chatbot_key(chatbot_id):
    return f"{settings.PRESENCE_REDIS_PREFIX}:chatbot:{chatbot_id}"


def _publish_widget_count(chatbot_id, online_count):
    event = {
        "type": PRESENCE_COUNT,
        "data": {"online_count": int(online_count)},
    }
    broadcast(
        [chatbot_widget_group(chatbot_id)],
        {
            "type": "widget.presence.event",
            "event": event,
        },
    )


def chatbot_online_count(chatbot_id):
    client = get_client()
    return int(
        client.register_script(_COUNT)(
            keys=[chatbot_key(chatbot_id)],
            args=[settings.PRESENCE_TIMEOUT_SECONDS],
        )
    )


def heartbeat(user_id, workspace_ids, chatbot_memberships=None):
    client = get_client()
    script = client.register_script(_HEARTBEAT)
    snapshots = []
    for workspace_id in sorted(workspace_ids):
        timestamp, became_online, user_ids = script(
            keys=[workspace_key(workspace_id)],
            args=[settings.PRESENCE_TIMEOUT_SECONDS, str(user_id)],
        )
        if became_online:
            publish_dashboard_event(
                workspace_dashboard_group(workspace_id),
                {
                    "type": MEMBER_ONLINE,
                    "data": {
                        "workspace_id": str(workspace_id),
                        "user_id": str(user_id),
                        "version": timestamp,
                    },
                },
            )
        snapshots.append(
            {
                "type": PRESENCE_SNAPSHOT,
                "data": {
                    "workspace_id": str(workspace_id),
                    "user_ids": user_ids,
                    "version": timestamp,
                },
            }
        )
    for chatbot_id, membership_id in sorted((chatbot_memberships or {}).items()):
        timestamp, became_online, member_ids = script(
            keys=[chatbot_key(chatbot_id)],
            args=[settings.PRESENCE_TIMEOUT_SECONDS, str(membership_id)],
        )
        if became_online:
            publish_dashboard_event(
                chatbot_dashboard_group(chatbot_id),
                {
                    "type": MEMBER_ONLINE,
                    "data": {
                        "chatbot_id": str(chatbot_id),
                        "member_id": str(membership_id),
                        "user_id": str(user_id),
                        "version": timestamp,
                    },
                },
            )
            _publish_widget_count(chatbot_id, len(member_ids))
        snapshots.append({
            "type": PRESENCE_SNAPSHOT,
            "data": {
                "chatbot_id": str(chatbot_id),
                "member_ids": member_ids,
                "version": timestamp,
            },
        })
    return snapshots


def expire_stale_presence():
    client = get_client()
    script = client.register_script(_EXPIRE)
    root_prefix = f"{settings.PRESENCE_REDIS_PREFIX}:"
    expired_count = 0
    for key in client.scan_iter(match=f"{root_prefix}*", count=100):
        scope_and_id = key[len(root_prefix):]
        try:
            scope, scope_id = scope_and_id.split(":", 1)
        except ValueError:
            continue
        if scope not in {"workspace", "chatbot"}:
            continue
        while True:
            timestamp, online_count, presence_ids = script(
                keys=[key], args=[settings.PRESENCE_TIMEOUT_SECONDS],
            )
            for presence_id in presence_ids:
                if scope == "workspace":
                    group = workspace_dashboard_group(scope_id)
                    data = {"workspace_id": scope_id, "user_id": presence_id}
                else:
                    group = chatbot_dashboard_group(scope_id)
                    data = {"chatbot_id": scope_id, "member_id": presence_id}
                publish_dashboard_event(
                    group,
                    {
                        "type": MEMBER_OFFLINE,
                        "data": {**data, "version": timestamp},
                    },
                )
            if scope == "chatbot" and presence_ids:
                _publish_widget_count(scope_id, online_count)
            expired_count += len(presence_ids)
            if len(presence_ids) < 500:
                break
    return expired_count
