"""Tests for the edge's upload loop: chunks, back-off and the buffer limit."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import edge_node  # noqa: E402


@pytest.fixture(autouse=True)
def clean_buffer():
    edge_node.buffer.clear()
    yield
    edge_node.buffer.clear()


def test_back_off_grows_then_stops_at_five_minutes(monkeypatch):
    monkeypatch.setattr(config, "CLOUD_BATCH_PERIOD_S", 10)
    assert [edge_node.next_delay(n) for n in range(0, 7)] == [10, 20, 40, 80, 160, 300, 300]


def test_buffer_is_sent_in_chunks(monkeypatch):
    sent = []
    monkeypatch.setattr(edge_node, "send_to_cloud", lambda batch: sent.append(len(batch)) or "test")
    for i in range(1200):
        edge_node.add_to_buffer({"type": "telemetry", "uav_id": "u", "ts": i})
    assert edge_node.flush_buffer() == 1200
    assert sent == [500, 500, 200] and edge_node.buffer == []


def test_failed_upload_keeps_the_unsent_records(monkeypatch):
    calls = []

    def flaky(batch):
        calls.append(len(batch))
        if len(calls) == 2:
            raise RuntimeError("cloud down")
        return "test"

    monkeypatch.setattr(edge_node, "send_to_cloud", flaky)
    for i in range(1200):
        edge_node.add_to_buffer({"type": "sensor", "uav_id": "u", "ts": i})
    with pytest.raises(RuntimeError):
        edge_node.flush_buffer()
    assert len(edge_node.buffer) == 700                      # first 500 sent, the rest kept
    assert edge_node.buffer[0]["ts"] == 500                  # in the right order


def test_buffer_never_grows_past_the_limit(monkeypatch):
    monkeypatch.setattr(edge_node, "MAX_BUFFER", 100)
    for i in range(250):
        edge_node.add_to_buffer({"type": "sensor", "uav_id": "u", "ts": i})
    assert len(edge_node.buffer) == 100 and edge_node.buffer[0]["ts"] == 150   # oldest dropped
