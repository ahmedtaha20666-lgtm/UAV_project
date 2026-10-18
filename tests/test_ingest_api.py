"""Tests for the cloud server program (api/ingest.py), using a fake local Firebase."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import edge_node  # noqa: E402
from api import ingest  # noqa: E402
from fake_firebase import API_KEY, EMAIL, PASSWORD, FakeFirebase  # noqa: E402

SECRET = "edge-secret-123"
BATCH = [
    {"type": "telemetry", "uav_id": "uav1", "ts": 1791640415.354, "battery": 99.5},
    {"type": "sensor", "uav_id": "uav1", "ts": 1791640415.354, "crowd": {"north": 5, "south": 9}},
]


@pytest.fixture()
def server(fake, monkeypatch):
    """The cloud server program, configured to use the fake Firebase."""
    for name, value in {"CLOUD_API_KEY": SECRET, "FIREBASE_DB_URL": fake, "FIREBASE_API_KEY": API_KEY,
                        "FIREBASE_EMAIL": EMAIL, "FIREBASE_PASSWORD": PASSWORD}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(ingest, "_client", None)
    monkeypatch.setattr(ingest, "_paused_until", 0.0)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ingest.handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/api/ingest"
    srv.shutdown()


def post(url, payload, key=SECRET, raw=None):
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-API-Key"] = key
    data = raw if raw is not None else json.dumps(payload).encode()
    return urllib.request.urlopen(urllib.request.Request(url, data, headers, method="POST"), timeout=10)


def status_of(url, payload, **kw):
    try:
        return post(url, payload, **kw).status
    except urllib.error.HTTPError as e:
        return e.code


def test_health_check(server):
    body = json.loads(urllib.request.urlopen(server).read())
    assert body["status"] == "ok" and body["api_key_set"] is True


def test_valid_batch_is_stored_in_firebase(server):
    resp = post(server, BATCH)
    assert resp.status == 200 and json.loads(resp.read()) == {"stored": 2}
    assert len(FakeFirebase.db["records"]) == 2
    assert FakeFirebase.db["stats"]["total"] == 2


def test_counters_stay_correct_across_calls_and_server_instances(server, monkeypatch):
    post(server, BATCH)
    monkeypatch.setattr(ingest, "_client", None)             # a "new" serverless instance
    post(server, BATCH[:1])
    assert FakeFirebase.db["stats"]["total"] == 3 and FakeFirebase.db["stats"]["batches"] == 2


def test_wrong_or_missing_key_is_rejected(server):
    assert status_of(server, BATCH, key="wrong") == 401
    assert status_of(server, BATCH, key=None) == 401
    assert "records" not in FakeFirebase.db


def test_server_without_a_key_refuses_everything(server, monkeypatch):
    monkeypatch.delenv("CLOUD_API_KEY")
    assert status_of(server, BATCH, key="") == 500


@pytest.mark.parametrize("payload", [
    {"not": "a list"},
    [],
    ["text"],
    [{"type": "video", "uav_id": "u", "ts": 1}],
    [{"type": "sensor", "uav_id": "", "ts": 1}],
    [{"type": "sensor", "uav_id": "u", "ts": "yesterday"}],
    [{"type": "sensor", "uav_id": "u", "ts": 1, "bad.key": 1}],
    [{"type": "sensor", "uav_id": "u", "ts": 1, "crowd": [1, 2]}],
])
def test_invalid_payloads_are_rejected(server, payload):
    assert status_of(server, payload) == 400
    assert "records" not in FakeFirebase.db


def test_not_json_and_empty_body(server):
    assert status_of(server, None, raw=b"not json") == 400
    assert status_of(server, None, raw=b"") == 400


def test_unknown_path_is_404(server):
    assert status_of(server.replace("/api/ingest", "/other"), BATCH) == 404


def test_firebase_login_problem_returns_502(server, monkeypatch):
    monkeypatch.setenv("FIREBASE_PASSWORD", "wrong")
    assert status_of(server, BATCH) == 502


def test_missing_firebase_settings_returns_500(server, monkeypatch):
    monkeypatch.delenv("FIREBASE_DB_URL")
    assert status_of(server, BATCH) == 500


def test_edge_sends_to_the_cloud_server(server, monkeypatch):
    """Edge (HTTP mode) -> cloud server program -> Firebase."""
    monkeypatch.setattr(config, "FIREBASE_DB_URL", "")
    monkeypatch.setattr(config, "CLOUD_URL", server)
    monkeypatch.setattr(config, "CLOUD_API_KEY", SECRET)
    assert edge_node.send_to_cloud(BATCH) == "HTTP 200"
    assert len(FakeFirebase.db["records"]) == 2


def test_edge_keeps_data_when_server_rejects(server, monkeypatch):
    monkeypatch.setattr(config, "FIREBASE_DB_URL", "")
    monkeypatch.setattr(config, "CLOUD_URL", server)
    monkeypatch.setattr(config, "CLOUD_API_KEY", "wrong")
    with pytest.raises(RuntimeError, match="HTTP 401: invalid or missing API key"):
        edge_node.send_to_cloud(BATCH)                       # the edge then keeps the batch


def test_edge_shows_why_firebase_login_failed(server, monkeypatch):
    monkeypatch.setattr(config, "FIREBASE_DB_URL", "")
    monkeypatch.setattr(config, "CLOUD_URL", server)
    monkeypatch.setattr(config, "CLOUD_API_KEY", SECRET)
    monkeypatch.setenv("FIREBASE_PASSWORD", "wrong")
    with pytest.raises(RuntimeError, match="HTTP 502: .*Wrong email or password"):
        edge_node.send_to_cloud(BATCH)


def test_server_pauses_after_a_failed_sign_in(server, monkeypatch):
    monkeypatch.setenv("FIREBASE_PASSWORD", "wrong")
    assert status_of(server, BATCH) == 502
    attempts = FakeFirebase.sign_ins
    try:
        post(server, BATCH)
    except urllib.error.HTTPError as e:
        assert e.code == 502
        assert "pausing" in json.loads(e.read())["error"]
    assert FakeFirebase.sign_ins == attempts                 # Firebase was NOT contacted again
    monkeypatch.setenv("FIREBASE_PASSWORD", PASSWORD)
    monkeypatch.setattr(ingest, "_paused_until", 0.0)        # the pause is over
    assert status_of(server, BATCH) == 200
