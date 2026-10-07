"""Ephemeral hybrid chatbot presence: immediate disconnect cleanup plus heartbeat fallback.

Redis TIME avoids worker clock skew. Lua serializes transitions against heartbeats.
Presence is tracked per WebSocket connection (`connection_key` ZSETs), so a chatbot
member leaves a scope only when their last connection disappears. Normal disconnects
are handled inline via `remove_presence`; crashed/sleeping clients are garbage
collected by the Celery sweep, which visits only scopes listed in the `active_scopes`
set. No database writes.
"""
from functools import lru_cache
from uuid import uuid4

from django.conf import settings
from redis import Redis

from base.socket.events import (
    MEMBER_OFFLINE,
    MEMBER_ONLINE,
    PRESENCE_COUNT,
    PRESENCE_SNAPSHOT,
)
from base.socket.services.broadcaster import broadcast, publish_dashboard_event
from base.socket.services.groups import chatbot_dashboard_group, chatbot_widget_group

# Members are fetched in bounded batches so one sweep call stays latency-friendly.
_SWEEP_BATCH = 500

_CLOCK = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000000 + tonumber(clock[2])
local cutoff = now - tonumber(ARGV[1]) * 1000000
"""
# KEYS[1]=chatbot zset KEYS[2]=per-member connections zset KEYS[3]=active scopes set
# ARGV[1]=timeout seconds ARGV[2]=membership id ARGV[3]=connection id
_HEARTBEAT = _CLOCK + """
local previous = redis.call('ZSCORE', KEYS[1], ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', cutoff)
redis.call('ZADD', KEYS[1], now, ARGV[2])
redis.call('ZADD', KEYS[2], now, ARGV[3])
redis.call('SADD', KEYS[3], KEYS[1])
local online = 0
if not previous or tonumber(previous) <= cutoff then online = 1 end
local members = redis.call('ZRANGEBYSCORE', KEYS[1], '(' .. cutoff, '+inf')
return {now, online, members}
"""
# Removes one connection; the member goes offline only with their last connection.
_DISCONNECT = _CLOCK + """
local previous = redis.call('ZSCORE', KEYS[1], ARGV[2])
redis.call('ZREM', KEYS[2], ARGV[3])
local went_offline = 0
if redis.call('ZCARD', KEYS[2]) == 0 then
    redis.call('DEL', KEYS[2])
    redis.call('ZREM', KEYS[1], ARGV[2])
    if previous then went_offline = 1 end
    if redis.call('ZCARD', KEYS[1]) == 0 then
        redis.call('DEL', KEYS[1])
        redis.call('SREM', KEYS[3], KEYS[1])
    end
end
local online_count = redis.call('ZCOUNT', KEYS[1], '(' .. cutoff, '+inf')
return {now, went_offline, online_count}
"""
# Candidates for the sweep: members whose latest heartbeat predates the cutoff.
_STALE = _CLOCK + """
return redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', cutoff, 'LIMIT', 0, ARGV[2])
"""
# Drops a member once every connection entry has expired; legacy members without
# a connections key (pre-upgrade data) are expired straight from the chatbot zset.
_EXPIRE = _CLOCK + """
local went_offline = 0
if redis.call('EXISTS', KEYS[2]) == 0 then
    if redis.call('ZREM', KEYS[1], ARGV[2]) == 1 then went_offline = 1 end
else
    redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', cutoff)
    if redis.call('ZCARD', KEYS[2]) == 0 then
        redis.call('DEL', KEYS[2])
        redis.call('ZREM', KEYS[1], ARGV[2])
        went_offline = 1
    end
end
if went_offline == 1 and redis.call('ZCARD', KEYS[1]) == 0 then
    redis.call('DEL', KEYS[1])
    redis.call('SREM', KEYS[3], KEYS[1])
end
local online_count = redis.call('ZCOUNT', KEYS[1], '(' .. cutoff, '+inf')
return {now, went_offline, online_count}
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


def chatbot_key(chatbot_id):
    return f"{settings.PRESENCE_REDIS_PREFIX}:chatbot:{chatbot_id}"


def active_scopes_key():
    return f"{settings.PRESENCE_REDIS_PREFIX}:active_scopes"


def connection_key(scope_key, presence_id):
    """Per member per chatbot: connection id -> last heartbeat, in Redis TIME."""
    return f"{scope_key}:conn:{presence_id}"


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


def _publish_member_offline(chatbot_id, membership_id, timestamp, online_count):
    publish_dashboard_event(
        chatbot_dashboard_group(chatbot_id),
        {
            "type": MEMBER_OFFLINE,
            "data": {
                "chatbot_id": str(chatbot_id),
                "member_id": membership_id,
                "version": timestamp,
            },
        },
    )
    _publish_widget_count(chatbot_id, online_count)


def _chatbot_scopes(chatbot_memberships):
    """Deterministic (chatbot id, chatbot key, membership id) tuples for one user."""
    for chatbot_id, membership_id in sorted((chatbot_memberships or {}).items()):
        yield str(chatbot_id), chatbot_key(chatbot_id), str(membership_id)


def chatbot_online_count(chatbot_id):
    client = get_client()
    return int(
        client.register_script(_COUNT)(
            keys=[chatbot_key(chatbot_id)],
            args=[settings.PRESENCE_TIMEOUT_SECONDS],
        )
    )


def heartbeat(user_id, chatbot_memberships=None, connection_id=None):
    client = get_client()
    script = client.register_script(_HEARTBEAT)
    connection_id = connection_id or uuid4().hex
    active_key = active_scopes_key()
    snapshots = []
    for chatbot_id, scope_key, membership_id in _chatbot_scopes(chatbot_memberships):
        timestamp, became_online, member_ids = script(
            keys=[scope_key, connection_key(scope_key, membership_id), active_key],
            args=[settings.PRESENCE_TIMEOUT_SECONDS, membership_id, connection_id],
        )
        if became_online:
            publish_dashboard_event(
                chatbot_dashboard_group(chatbot_id),
                {
                    "type": MEMBER_ONLINE,
                    "data": {
                        "chatbot_id": chatbot_id,
                        "member_id": membership_id,
                        "user_id": str(user_id),
                        "version": timestamp,
                    },
                },
            )
            _publish_widget_count(chatbot_id, len(member_ids))
        snapshots.append(
            {
                "type": PRESENCE_SNAPSHOT,
                "data": {
                    "chatbot_id": chatbot_id,
                    "member_ids": member_ids,
                    "version": timestamp,
                },
            }
        )
    return snapshots


def remove_presence(chatbot_memberships=None, connection_id=None):
    """Immediate path: forget one connection; emit offline on the last removal."""
    if not connection_id:
        return 0
    client = get_client()
    script = client.register_script(_DISCONNECT)
    active_key = active_scopes_key()
    removed_scopes = 0
    for chatbot_id, scope_key, membership_id in _chatbot_scopes(chatbot_memberships):
        timestamp, went_offline, online_count = script(
            keys=[scope_key, connection_key(scope_key, membership_id), active_key],
            args=[settings.PRESENCE_TIMEOUT_SECONDS, membership_id, connection_id],
        )
        if went_offline:
            removed_scopes += 1
            _publish_member_offline(chatbot_id, membership_id, timestamp, online_count)
    return removed_scopes


def expire_stale_presence():
    """Fallback sweep for abnormal disconnects; touches active chatbot scopes only."""
    client = get_client()
    stale_script = client.register_script(_STALE)
    expire_script = client.register_script(_EXPIRE)
    active_key = active_scopes_key()
    expired_count = 0
    for scope_key in sorted(client.smembers(active_key)):
        scope, _, chatbot_id = scope_key[
            len(settings.PRESENCE_REDIS_PREFIX) + 1:
        ].partition(":")
        if scope != "chatbot":
            client.srem(active_key, scope_key)  # Self-heal legacy/workspace entries.
            continue
        while True:
            stale_ids = stale_script(
                keys=[scope_key],
                args=[settings.PRESENCE_TIMEOUT_SECONDS, _SWEEP_BATCH],
            )
            for membership_id in stale_ids:
                timestamp, went_offline, online_count = expire_script(
                    keys=[scope_key, connection_key(scope_key, membership_id), active_key],
                    args=[settings.PRESENCE_TIMEOUT_SECONDS, membership_id],
                )
                if went_offline:
                    expired_count += 1
                    _publish_member_offline(chatbot_id, membership_id, timestamp, online_count)
            if len(stale_ids) < _SWEEP_BATCH:
                break
    return expired_count
