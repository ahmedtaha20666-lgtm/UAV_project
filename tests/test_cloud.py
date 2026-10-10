"""Tests for the cloud web server (cloud_node.py)."""
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cloud_node  # noqa: E402


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_node, "OUTPUT_FILE", str(tmp_path / "out.jsonl"))
    cloud_node.records.clear()
    cloud_node.stats.update(total=0, batches=0)
    srv = cloud_node.make_server(0)           # port 0 = any free port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(url, payload, key=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=5)


def test_health(server):
    assert urllib.request.urlopen(server + "/health").read() == b"ok"


def test_ingest_then_read_back(server):
    batch = [{"type": "telemetry", "uav_id": "u1", "battery": 90}]
    assert post(server + "/ingest", batch).status == 200
    body = json.loads(urllib.request.urlopen(server + "/data").read())
    assert body["total_received"] == 1
    assert body["records"][0]["uav_id"] == "u1"
    page = urllib.request.urlopen(server + "/").read().decode()
    assert "Total records received:</b> 1" in page


def test_api_key_is_enforced(server, monkeypatch):
    monkeypatch.setenv("CLOUD_API_KEY", "secret123")
    batch = [{"type": "sensor", "uav_id": "u1"}]
    with pytest.raises(urllib.error.HTTPError) as err:
        post(server + "/ingest", batch)                 # no key
    assert err.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as err:
        post(server + "/ingest", batch, key="wrong")    # wrong key
    assert err.value.code == 401
    assert post(server + "/ingest", batch, key="secret123").status == 200


def test_bad_payload_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as err:
        post(server + "/ingest", {"not": "a list"})
    assert err.value.code == 400
