"""Self-verification for lwe_ui.api_client - the engine daemon API courier.

Headless + isolated like the other suites: LWE_SOCKET points every test at a socket in
its own tempdir, so nothing here can ever reach a real engine. A scripted fake engine
(one thread, one accept) plays the wire roles: acks, dones, errors, garbage, silence.

Contract under test:
  * available() is False for a missing path and a dead socket file, True for a listener;
  * show() default waits ONLY for the accepted ack (the click-handler budget);
  * request(wait_done=True) skips past "accepted" to the final reply;
  * engine rejection (ok=false) comes back as a dict - distinguishable from None
    (unreachable / garbage / closed mid-reply), and the courier NEVER raises.

Run: export PYTHONPATH=src && python3 tests/test_api_client.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from lwe_ui import api_client  # noqa: E402


class FakeEngine:
    """One-shot scripted server: accepts one client, replies with the given lines."""

    def __init__(self, sock_path: Path, replies: list[str], close_after: bool = True):
        self.path = sock_path
        self.replies = replies
        self.close_after = close_after
        self.received: list[str] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(sock_path))
        self._server.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._server.accept()
            conn.settimeout(5)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            self.received.append(buf.decode(errors="replace").strip())
            for line in self.replies:
                conn.sendall((line + "\n").encode())
            if self.close_after:
                conn.close()
            else:
                # hold the connection open past the client's interest
                threading.Event().wait(2)
                conn.close()
        except OSError:
            pass

    def stop(self) -> None:
        try:
            self._server.close()
        except OSError:
            pass


class ApiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="lwe-api-client-")
        self.sock = Path(self._dir.name) / "engine.sock"
        os.environ["LWE_SOCKET"] = str(self.sock)

    def tearDown(self) -> None:
        os.environ["LWE_SOCKET"] = _sandbox.SOCKET
        self._dir.cleanup()


    def test_available_false_when_missing(self) -> None:
        self.assertFalse(api_client.available())

    def test_available_false_for_dead_socket_file(self) -> None:
        # a socket file nobody listens on: what a crashed engine leaves behind
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(self.sock))
        stale.close()  # closed WITHOUT listen/accept: connect must fail
        self.assertFalse(api_client.available())

    def test_available_true_for_listener(self) -> None:
        engine = FakeEngine(self.sock, [])
        try:
            self.assertTrue(api_client.available())
        finally:
            engine.stop()


    def test_show_returns_on_ack_alone(self) -> None:
        # only the ack is scripted; if show() waited for done it would time out
        engine = FakeEngine(self.sock, ['{"id":1,"ok":true,"status":"accepted"}'], close_after=False)
        try:
            reply = api_client.show("3134543499")
            self.assertIsNotNone(reply)
            self.assertTrue(reply["ok"])
            self.assertEqual(reply["status"], "accepted")
            sent = json.loads(engine.received[0])
            self.assertEqual(sent["cmd"], "show")
            self.assertEqual(sent["args"]["id"], "3134543499")
        finally:
            engine.stop()

    def test_show_wait_done_skips_the_ack(self) -> None:
        engine = FakeEngine(
            self.sock,
            [
                '{"id":1,"ok":true,"status":"accepted"}',
                '{"id":1,"ok":true,"status":"done","result":{"path":"/x"}}',
            ],
        )
        try:
            reply = api_client.show("3134543499", wait_done=True)
            self.assertIsNotNone(reply)
            self.assertEqual(reply["status"], "done")
            self.assertEqual(reply["result"]["path"], "/x")
        finally:
            engine.stop()


    def test_engine_rejection_is_a_dict_not_none(self) -> None:
        engine = FakeEngine(self.sock, ['{"id":1,"ok":false,"error":"background not found"}'])
        try:
            reply = api_client.show("0000000000")
            self.assertIsNotNone(reply)
            self.assertFalse(reply["ok"])
        finally:
            engine.stop()

    def test_garbage_reply_is_none(self) -> None:
        engine = FakeEngine(self.sock, ["this is not json"])
        try:
            self.assertIsNone(api_client.show("3134543499"))
        finally:
            engine.stop()

    def test_connection_closed_before_reply_is_none(self) -> None:
        engine = FakeEngine(self.sock, [])
        try:
            self.assertIsNone(api_client.show("3134543499"))
        finally:
            engine.stop()


    def test_status_unwraps_result(self) -> None:
        engine = FakeEngine(
            self.sock,
            ['{"id":1,"ok":true,"status":"done","result":{"api":1,"screens":{"DP-1":"/x"}}}'],
        )
        try:
            result = api_client.status()
            self.assertIsNotNone(result)
            self.assertEqual(result["screens"]["DP-1"], "/x")
        finally:
            engine.stop()

    def test_status_none_when_unreachable(self) -> None:
        self.assertIsNone(api_client.status())

    def test_playlist_set_sends_the_documented_shape(self) -> None:
        engine = FakeEngine(self.sock, ['{"id":1,"ok":true,"status":"done","result":{"slug":"chill","count":1,"bound":false}}'])
        try:
            reply = api_client.playlist_set("chill", [{"id": "1", "ui_id": "1"}], "sequential", 5,
                                            part=2, of=3, avoid_repeat=False, label="\u00e9" * 200)
        finally:
            engine.stop()
        self.assertEqual(reply["result"]["slug"], "chill")
        sent = json.loads(engine.received[0])
        self.assertEqual(sent["cmd"], "playlist-set")
        self.assertEqual(sent["args"]["slug"], "chill")
        self.assertEqual(sent["args"]["entries"], [{"id": "1", "ui_id": "1"}])
        self.assertEqual((sent["args"]["part"], sent["args"]["of"]), (2, 3))
        self.assertEqual(sent["args"]["interval_s"], 15, "the engine floors interval at 15 s")
        self.assertEqual(sent["args"]["order"], "sequential")
        self.assertIs(sent["args"]["avoid_repeat"], False)
        # the engine caps the label at 128 BYTES; a two-byte character must not be split
        self.assertEqual(sent["args"]["label"], "\u00e9" * 64)
        self.assertEqual(len(sent["args"]["label"].encode("utf-8")), 128)

    def test_playlist_set_clamps_the_interval_ceiling(self) -> None:
        engine = FakeEngine(self.sock, ['{"id":1,"ok":true,"status":"done","result":{}}'])
        try:
            api_client.playlist_set("chill", [], "shuffle", 10 ** 9)
        finally:
            engine.stop()
        self.assertEqual(json.loads(engine.received[0])["args"]["interval_s"], 604800)

    def test_lanes_set_sends_the_lane_list(self) -> None:
        engine = FakeEngine(self.sock, ['{"id":1,"ok":true,"status":"done","result":{"lanes":[]}}'])
        lanes = [{"id": "all", "playlist": "chill", "enabled": True, "fit": {"zoom": 1.5}}]
        try:
            reply = api_client.lanes_set(lanes)
        finally:
            engine.stop()
        self.assertEqual(reply["result"], {"lanes": []})
        sent = json.loads(engine.received[0])
        self.assertEqual(sent["cmd"], "lanes-set")
        self.assertEqual(sent["args"], {"lanes": lanes})

    def test_engine_refusal_of_a_verb_is_a_dict(self) -> None:
        engine = FakeEngine(self.sock, ['{"id":1,"ok":false,"error":"unknown playlist"}'])
        try:
            reply = api_client.lanes_set([{"id": "all", "playlist": "ghost"}])
        finally:
            engine.stop()
        self.assertEqual(reply["ok"], False)
        self.assertIn("unknown playlist", reply["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
