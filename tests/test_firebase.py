"""Tests for the Firebase client, using a fake local Firebase (no internet needed).

The fake server imitates:
  * email/password sign-in            POST /auth?key=...
  * Realtime Database REST + rules    GET/PATCH /<path>.json?auth=<token>
    (writes are refused without a valid token, like the real rules)
"""
import json
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import edge_node  # noqa: E402
import firebase_client  # noqa: E402
from firebase_client import FirebaseClient, make_key  # noqa: E402

API_KEY, EMAIL, PASSWORD = "test-key", "edge@uav.local", "s3cret"


class FakeFirebase(BaseHTTPRequestHandler):
    db = {}          # the fake database  {"records": {...}, "stats": {...}}
    tokens = set()
    sign_ins = 0

    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))

    def do_POST(self):                      # sign in
        url = urlparse(self.path)
        body = self._body()
        if parse_qs(url.query).get("key") != [API_KEY]:
            return self._reply(400, {"error": {"message": "API_KEY_INVALID"}})
        if body["email"] != EMAIL or body["password"] != PASSWORD:
            return self._reply(400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}})
        FakeFirebase.sign_ins += 1
        token = f"token{FakeFirebase.sign_ins}"
        FakeFirebase.tokens.add(token)
        self._reply(200, {"idToken": token, "expiresIn": "3600"})

    def _db(self, method):
        url = urlparse(self.path)
        node = url.path.strip("/").removesuffix(".json")
        token = parse_qs(url.query).get("auth", [None])[0]
        if method == "PATCH" and token not in FakeFirebase.tokens:    # rules: write needs auth
            return self._reply(401, {"error": "Permission denied"})
        if method == "GET":
            return self._reply(200, FakeFirebase.db.get(node))
        FakeFirebase.db.setdefault(node, {}).update(self._body())
        self._reply(200, {})

    def do_GET(self):
        self._db("GET")

    def do_PATCH(self):
        self._db("PATCH")

    def log_message(self, *args):
        pass


@pytest.fixture()
def fake():
    FakeFirebase.db, FakeFirebase.tokens, FakeFirebase.sign_ins = {}, set(), 0
    srv = HTTPServer(("127.0.0.1", 0), FakeFirebase)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    old = firebase_client.AUTH_URL
    firebase_client.AUTH_URL = base + "/auth"
    yield base
    firebase_client.AUTH_URL = old
    srv.shutdown()


BATCH = [
    {"type": "telemetry", "uav_id": "uav1", "ts": 1791640415.354, "battery": 99.5},
    {"type": "sensor", "uav_id": "uav1", "ts": 1791640415.354, "crowd": {"north": 5}},
    {"type": "telemetry", "uav_id": "uav1", "ts": 1791640415.854, "battery": 99.4},
]


def test_make_key_is_sortable_unique_and_repeatable():
    used = set()
    k1 = make_key(BATCH[0], used)
    k2 = make_key(BATCH[1], used)
    assert k1 == "1791640415354_uav1_telemetry" and k2 == "1791640415354_uav1_sensor"
    assert make_key(BATCH[0], used) == k1 + "_2"             # duplicate inside one batch
    assert make_key(BATCH[0], set()) == k1                   # same record -> same key on retry
    assert make_key({"uav_id": "a.b/c", "ts": 1.0, "type": "x"}, set()) == "0000000001000_a_b_c_x"


def test_send_batch_stores_records_and_stats(fake):
    client = FirebaseClient(fake, API_KEY, EMAIL, PASSWORD)
    assert client.send_batch(BATCH) == 3
    assert len(FakeFirebase.db["records"]) == 3
    stats = FakeFirebase.db["stats"]
    assert (stats["total"], stats["batches"], stats["telemetry"], stats["sensor"]) == (3, 1, 2, 1)
    client.send_batch(BATCH[:1])
    assert FakeFirebase.db["stats"]["total"] == 4 and FakeFirebase.db["stats"]["batches"] == 2
    assert FakeFirebase.sign_ins == 1                        # token is reused


def test_counters_continue_after_edge_restart(fake):
    FirebaseClient(fake, API_KEY, EMAIL, PASSWORD).send_batch(BATCH)
    FirebaseClient(fake, API_KEY, EMAIL, PASSWORD).send_batch(BATCH[:2])   # "restarted" edge
    assert FakeFirebase.db["stats"]["total"] == 5


def test_retry_does_not_duplicate_records(fake):
    client = FirebaseClient(fake, API_KEY, EMAIL, PASSWORD)
    client.send_batch(BATCH)
    client._stats = None                                     # pretend the stats update was lost
    client.send_batch(BATCH)                                 # the edge re-sends the same batch
    assert len(FakeFirebase.db["records"]) == 3


def test_expired_token_signs_in_again(fake):
    client = FirebaseClient(fake, API_KEY, EMAIL, PASSWORD)
    client.send_batch(BATCH[:1])
    FakeFirebase.tokens.clear()                              # server forgets the token
    client.send_batch(BATCH[1:2])
    assert FakeFirebase.sign_ins == 2


def test_wrong_password_gives_clear_error(fake):
    client = FirebaseClient(fake, API_KEY, EMAIL, "wrong")
    with pytest.raises(RuntimeError, match="Wrong email or password"):
        client.send_batch(BATCH)


def test_wrong_api_key_gives_clear_error(fake):
    client = FirebaseClient(fake, "bad-key", EMAIL, PASSWORD)
    with pytest.raises(RuntimeError, match="Web API key"):
        client.send_batch(BATCH)


def test_edge_uses_firebase_when_configured(fake, monkeypatch):
    monkeypatch.setattr(config, "FIREBASE_DB_URL", fake)
    monkeypatch.setattr(config, "FIREBASE_API_KEY", API_KEY)
    monkeypatch.setattr(config, "FIREBASE_EMAIL", EMAIL)
    monkeypatch.setattr(config, "FIREBASE_PASSWORD", PASSWORD)
    monkeypatch.setattr(edge_node, "_firebase", None)
    assert edge_node.send_to_cloud(BATCH) == "Firebase"
    assert len(FakeFirebase.db["records"]) == 3


def test_edge_raises_when_cloud_is_down(monkeypatch):
    monkeypatch.setattr(config, "FIREBASE_DB_URL", "")
    monkeypatch.setattr(config, "CLOUD_URL", "http://127.0.0.1:9/ingest")   # nothing listens here
    with pytest.raises(urllib.error.URLError):
        edge_node.send_to_cloud(BATCH)
