"""Ephemeral, aggregate user presence. No disconnect mutation or database writes.

Redis TIME avoids worker clock skew. Lua serializes transitions against heartbeats.
Empty sorted sets disappear during the sweep, without a growing workspace registry.
"""
from functools import lru_cache

from django.conf import settings
from redis import Redis

from base.socket.events import MEMBER_OFFLINE, MEMBER_ONLINE, PRESENCE_SNAPSHOT
from base.socket.services.broadcaster import publish_dashboard_event
from base.socket.services.groups import workspace_dashboard_group

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
return {now, users}
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


def heartbeat(user_id, workspace_ids):
    client = get_client()
    script = client.register_script(_HEARTBEAT)
    snapshots = []
    for workspace_id in sorted(workspace_ids):
        timestamp, became_online, user_ids = script(
            keys=[workspace_key(workspace_id)],
            args=[settings.PRESENCE_TIMEOUT_SECONDS, str(user_id)],
        )
        if became_online:
            publish_dashboard_event(workspace_dashboard_group(workspace_id), {
                "type": MEMBER_ONLINE,
                "data": {"workspace_id": str(workspace_id), "user_id": str(user_id),
                         "version": timestamp},
            })
        snapshots.append({
            "type": PRESENCE_SNAPSHOT,
            "data": {"workspace_id": str(workspace_id), "user_ids": user_ids,
                     "version": timestamp},
        })
    return snapshots


def expire_stale_presence():
    client = get_client()
    script = client.register_script(_EXPIRE)
    prefix = f"{settings.PRESENCE_REDIS_PREFIX}:workspace:"
    expired_count = 0
    for key in client.scan_iter(match=f"{prefix}*", count=100):
        workspace_id = key[len(prefix):]
        while True:
            timestamp, user_ids = script(
                keys=[key], args=[settings.PRESENCE_TIMEOUT_SECONDS],
            )
            for user_id in user_ids:
                publish_dashboard_event(workspace_dashboard_group(workspace_id), {
                    "type": MEMBER_OFFLINE,
                    "data": {"workspace_id": workspace_id, "user_id": user_id,
                             "version": timestamp},
                })
            expired_count += len(user_ids)
            if len(user_ids) < 500:
                break
    return expired_count
