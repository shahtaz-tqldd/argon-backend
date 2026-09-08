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
        self.addCleanup(patch.stopall)

    def age_user(self, workspace="workspace-a", user="user-a", seconds=80):
        sec, micros = self.redis.time()
        self.redis.zadd(presence.workspace_key(workspace), {
            user: sec * 1000000 + micros - seconds * 1000000,
        })

    def test_snapshot_is_workspace_scoped_and_heartbeats_do_not_repeat_online(self):
        presence.heartbeat("user-b", {"workspace-b"})
        snapshot = presence.heartbeat("user-a", {"workspace-a"})[0]
        self.assertEqual(snapshot["type"], "presence.snapshot")
        self.assertEqual(snapshot["data"]["user_ids"], ["user-a"])
        presence.heartbeat("user-a", {"workspace-a"})
        self.assertEqual(self.events.call_count, 2)

    def test_crash_expires_once_and_removes_empty_workspace(self):
        presence.heartbeat("user-a", {"workspace-a"})
        self.age_user()
        self.assertEqual(presence.expire_stale_presence(), 1)
        self.assertEqual(presence.expire_stale_presence(), 0)
        self.assertFalse(self.redis.exists(presence.workspace_key("workspace-a")))
        self.assertEqual(self.events.call_args.args[1]["type"], "member.offline")

    def test_other_tab_heartbeat_keeps_user_online(self):
        presence.heartbeat("user-a", {"workspace-a"})
        self.age_user(seconds=60)
        presence.heartbeat("user-a", {"workspace-a"})
        self.assertEqual(presence.expire_stale_presence(), 0)
        self.assertEqual(self.events.call_count, 1)

    def test_concurrent_connections_emit_one_online_transition(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: presence.heartbeat("user-a", {"workspace-a"}), range(16)))
        self.assertEqual(self.events.call_count, 1)

    def test_concurrent_sweepers_emit_one_offline_transition(self):
        self.age_user()
        with ThreadPoolExecutor(max_workers=8) as executor:
            counts = list(executor.map(lambda _: presence.expire_stale_presence(), range(16)))
        self.assertEqual(sum(counts), 1)
        self.assertEqual(self.events.call_count, 1)

    def test_expired_reconnect_is_online_and_snapshot_excludes_other_stale_users(self):
        self.age_user()
        self.age_user(user="stale-user")
        snapshot = presence.heartbeat("user-a", {"workspace-a"})[0]
        self.assertEqual(snapshot["data"]["user_ids"], ["user-a"])
        self.assertEqual(self.events.call_args.args[1]["type"], "member.online")
        self.assertEqual(presence.expire_stale_presence(), 1)
        self.assertEqual(self.events.call_args.args[1]["data"]["user_id"], "stale-user")
