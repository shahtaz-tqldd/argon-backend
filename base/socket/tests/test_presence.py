"""Exercise the actual Lua scripts against an isolated, disposable Redis process."""
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import SkipTest
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from redis import Redis
from redis.exceptions import ConnectionError

from base.socket.services import presence

MEMBERSHIPS = {"chatbot-a": "membership-a"}


@override_settings(PRESENCE_REDIS_PREFIX="socket-tests", PRESENCE_TIMEOUT_SECONDS=75)
class PresenceTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not shutil.which("redis-server"):
            raise SkipTest("redis-server is required for presence integration tests")
        cls.directory = tempfile.TemporaryDirectory(prefix="argon-presence-")
        cls.addClassCleanup(cls.directory.cleanup)
        socket_path = cls.directory.name + "/redis.sock"
        cls.process = subprocess.Popen([
            "redis-server", "--port", "0", "--unixsocket", socket_path,
            "--save", "", "--appendonly", "no", "--dir", cls.directory.name,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop_redis)
        cls.redis = Redis(unix_socket_path=socket_path, decode_responses=True)
        cls.addClassCleanup(cls.redis.close)
        for _ in range(100):
            try:
                cls.redis.ping()
                break
            except ConnectionError:
                time.sleep(0.02)
        else:
            raise RuntimeError("Disposable Redis failed to start")

    @classmethod
    def stop_redis(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)

    def setUp(self):
        self.redis.flushdb()  # This process belongs exclusively to this test class.
        self.client_patch = patch.object(presence, "get_client", return_value=self.redis)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.events = patch.object(presence, "publish_dashboard_event").start()
        self.broadcast = patch.object(presence, "broadcast").start()
        self.addCleanup(patch.stopall)

    def age_member(self, chatbot="chatbot-a", member="membership-a", seconds=80, connection=None):
        sec, micros = self.redis.time()
        score = sec * 1000000 + micros - seconds * 1000000
        self.redis.zadd(presence.chatbot_key(chatbot), {member: score})
        if connection is not None:
            self.redis.zadd(
                presence.connection_key(presence.chatbot_key(chatbot), member),
                {connection: score},
            )

    def age_connection(
        self, chatbot="chatbot-a", member="membership-a", connection="conn", seconds=80,
    ):
        sec, micros = self.redis.time()
        self.redis.zadd(
            presence.connection_key(presence.chatbot_key(chatbot), member),
            {connection: sec * 1000000 + micros - seconds * 1000000},
        )

    def test_snapshot_uses_membership_ids_and_heartbeats_do_not_repeat_online(self):
        presence.heartbeat("user-b", {"chatbot-b": "membership-b"}, connection_id="conn-b")
        snapshot = presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")[0]
        self.assertEqual(snapshot["type"], "presence.snapshot")
        self.assertEqual(snapshot["data"]["member_ids"], ["membership-a"])
        self.assertEqual(
            self.events.call_args.args[1]["data"]["member_id"],
            "membership-a",
        )
        self.assertEqual(
            self.broadcast.call_args.args[1]["event"],
            {"type": "presence.count", "data": {"online_count": 1}},
        )
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        self.assertEqual(self.events.call_count, 2)  # chatbot-a plus chatbot-b online.
        self.assertEqual(presence.chatbot_online_count("chatbot-a"), 1)

    def test_crash_expires_once_and_removes_empty_chatbot_scope(self):
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        self.age_member(connection="conn-a")
        self.assertEqual(presence.expire_stale_presence(), 1)
        self.assertEqual(presence.expire_stale_presence(), 0)
        self.assertFalse(self.redis.exists(presence.chatbot_key("chatbot-a")))
        self.assertFalse(
            self.redis.exists(
                presence.connection_key(presence.chatbot_key("chatbot-a"), "membership-a"),
            )
        )
        self.assertEqual(self.redis.smembers(presence.active_scopes_key()), set())
        self.assertEqual(self.events.call_args.args[1]["type"], "member.offline")

    def test_other_tab_heartbeat_keeps_member_online(self):
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        self.age_member(seconds=60, connection="conn-a")
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-b")
        self.assertEqual(presence.expire_stale_presence(), 0)
        self.assertEqual(self.events.call_count, 1)

    def test_concurrent_connections_emit_one_online_transition(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(
                lambda index: presence.heartbeat(
                    "user-a", MEMBERSHIPS, connection_id=f"conn-{index}",
                ),
                range(16),
            ))
        self.assertEqual(self.events.call_count, 1)

    def test_concurrent_sweepers_emit_one_offline_transition(self):
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        self.age_member(connection="conn-a")
        with ThreadPoolExecutor(max_workers=8) as executor:
            counts = list(executor.map(lambda _: presence.expire_stale_presence(), range(16)))
        self.assertEqual(sum(counts), 1)
        self.assertEqual(self.events.call_count, 2)  # member.online plus one member.offline.
        self.assertEqual(self.events.call_args.args[1]["type"], "member.offline")

    def test_expired_reconnect_is_online_and_snapshot_excludes_other_stale_members(self):
        self.age_member(connection="conn-a")
        self.age_member(member="stale-membership", connection="conn-stale")
        snapshot = presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")[0]
        self.assertEqual(snapshot["data"]["member_ids"], ["membership-a"])
        self.assertEqual(self.events.call_args.args[1]["type"], "member.online")
        self.assertEqual(presence.expire_stale_presence(), 1)
        self.assertEqual(
            self.events.call_args.args[1]["data"]["member_id"],
            "stale-membership",
        )

    def test_expiry_publishes_offline_and_zero_widget_count(self):
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        self.age_member(connection="conn-a")
        self.assertEqual(presence.expire_stale_presence(), 1)
        self.assertEqual(
            self.events.call_args.args[1]["data"]["member_id"],
            "membership-a",
        )
        self.assertEqual(
            self.broadcast.call_args.args[1]["event"]["data"]["online_count"],
            0,
        )

    def test_last_connection_disconnect_emits_offline_immediately(self):
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        self.assertEqual(presence.remove_presence(MEMBERSHIPS, connection_id="conn-a"), 1)
        self.assertFalse(self.redis.exists(presence.chatbot_key("chatbot-a")))
        self.assertFalse(
            self.redis.exists(
                presence.connection_key(presence.chatbot_key("chatbot-a"), "membership-a"),
            )
        )
        self.assertEqual(self.redis.smembers(presence.active_scopes_key()), set())
        self.assertEqual(self.events.call_args.args[1]["type"], "member.offline")
        self.assertEqual(
            self.broadcast.call_args.args[1]["event"]["data"]["online_count"],
            0,
        )
        self.assertEqual(presence.expire_stale_presence(), 0)

    def test_disconnect_with_other_live_tab_does_not_offline_member(self):
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-b")
        self.assertEqual(presence.remove_presence(MEMBERSHIPS, connection_id="conn-a"), 0)
        self.assertEqual(self.events.call_count, 1)  # Only the initial member.online.
        snapshot = presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-b")[0]
        self.assertEqual(snapshot["data"]["member_ids"], ["membership-a"])
        self.assertEqual(presence.remove_presence(MEMBERSHIPS, connection_id="conn-b"), 1)
        self.assertEqual(self.events.call_args.args[1]["type"], "member.offline")

    def test_disconnect_is_idempotent_and_ignores_unregistered_presence(self):
        self.assertEqual(presence.remove_presence(MEMBERSHIPS, connection_id="x"), 0)
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        presence.remove_presence(MEMBERSHIPS, connection_id="conn-a")
        self.assertEqual(presence.remove_presence(MEMBERSHIPS, connection_id="conn-a"), 0)
        self.assertEqual(self.events.call_count, 2)  # member.online then one member.offline.

    def test_stale_sockets_disconnecting_defer_offline_to_sweep(self):
        # Laptop sleep: both tabs stop heartbeating, then one socket times out.
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-crashed")
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-live")
        self.age_member()
        self.age_connection(connection="conn-crashed")
        self.age_connection(connection="conn-live")
        self.assertEqual(presence.remove_presence(MEMBERSHIPS, connection_id="conn-live"), 0)
        self.assertEqual(presence.expire_stale_presence(), 1)
        self.assertEqual(self.events.call_args.args[1]["type"], "member.offline")

    def test_immediate_offline_when_last_tab_closes_after_sibling_was_pruned(self):
        # The crashed tab's entry was pruned by heartbeats, so the live tab's
        # clean disconnect is the last connection and offlines immediately.
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-crashed")
        self.age_connection(connection="conn-crashed")
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-live")
        self.assertEqual(presence.remove_presence(MEMBERSHIPS, connection_id="conn-live"), 1)
        self.assertEqual(self.events.call_args.args[1]["type"], "member.offline")
        self.assertEqual(presence.expire_stale_presence(), 0)

    def test_heartbeat_prunes_stale_sibling_connections_of_same_member(self):
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-crashed")
        self.age_connection(connection="conn-crashed")
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-live")
        conn_key = presence.connection_key(
            presence.chatbot_key("chatbot-a"), "membership-a",
        )
        self.assertEqual(self.redis.zrange(conn_key, 0, -1), ["conn-live"])

    def test_sweep_returns_immediately_without_active_scopes(self):
        self.assertEqual(presence.expire_stale_presence(), 0)

    def test_sweep_only_visits_scopes_with_active_members(self):
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        presence.remove_presence(MEMBERSHIPS, connection_id="conn-a")
        self.age_member(chatbot="chatbot-orphan")  # Never heartbeated: not active.
        self.assertEqual(presence.expire_stale_presence(), 0)
        # No SCAN: the orphan key outside active_scopes is left untouched.
        self.assertTrue(self.redis.exists(presence.chatbot_key("chatbot-orphan")))

    def test_sweep_discards_non_chatbot_entries_from_active_scopes(self):
        self.redis.sadd(
            presence.active_scopes_key(),
            f"{presence.chatbot_key('chatbot-a')}",
            "socket-tests:workspace:legacy",
        )
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        self.assertEqual(presence.expire_stale_presence(), 0)
        self.assertEqual(
            self.redis.smembers(presence.active_scopes_key()),
            {presence.chatbot_key("chatbot-a")},
        )

    def test_scope_stays_active_while_other_members_remain(self):
        presence.heartbeat("user-a", MEMBERSHIPS, connection_id="conn-a")
        presence.heartbeat("user-b", {"chatbot-a": "membership-b"}, connection_id="conn-b")
        presence.remove_presence(MEMBERSHIPS, connection_id="conn-a")
        self.assertEqual(
            self.redis.smembers(presence.active_scopes_key()),
            {presence.chatbot_key("chatbot-a")},
        )
        self.assertEqual(
            self.redis.zrange(presence.chatbot_key("chatbot-a"), 0, -1),
            ["membership-b"],
        )

    def test_user_across_multiple_chatbots_presence_is_independent(self):
        memberships = {"chatbot-a": "membership-a", "chatbot-b": "membership-b"}
        presence.heartbeat("user-a", memberships, connection_id="conn-a")
        self.assertEqual(presence.remove_presence(memberships, connection_id="conn-a"), 2)
        self.assertFalse(self.redis.exists(presence.chatbot_key("chatbot-a")))
        self.assertFalse(self.redis.exists(presence.chatbot_key("chatbot-b")))
        self.assertEqual(self.redis.smembers(presence.active_scopes_key()), set())
        self.assertEqual(self.events.call_count, 4)  # Two online, two offline.
