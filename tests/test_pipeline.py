"""Tests for the UAV -> Edge -> Cloud pipeline.

1. Unit tests   : the decision logic (decide) in edge_node.py
2. Integration  : start broker + cloud + edge + UAV and check that data
                  really reaches the cloud file.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import edge_node  # noqa: E402


# ---------------------------------------------------------------- unit tests
def test_decide_moves_toward_most_crowded_zone():
    sensor = {"crowd": {"north": 10, "south": 90, "east": 20, "west": 30}}
    cmd = edge_node.decide("uav1", sensor)
    assert cmd["action"] == "move"
    assert (cmd["vx"], cmd["vy"]) == (0, -1)  # south


def test_decide_returns_home_on_low_battery():
    edge_node.latest["uav_low"] = {"battery": 10}
    sensor = {"crowd": {"north": 50, "south": 1, "east": 1, "west": 1}}
    cmd = edge_node.decide("uav_low", sensor)
    assert cmd["action"] == "return_home"


# --------------------------------------------------------- integration test
@pytest.mark.skipif(shutil.which("mosquitto") is None,
                    reason="mosquitto broker not installed")
def test_full_pipeline_delivers_data_to_cloud(tmp_path):
    env = dict(os.environ,
               CLOUD_BATCH_PERIOD_S="2",   # speed things up for the test
               SENSOR_PERIOD_S="0.5",
               PYTHONUNBUFFERED="1")
    procs = []

    def start(*cmd):
        p = subprocess.Popen(cmd, cwd=tmp_path, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        procs.append(p)
        return p

    try:
        start("mosquitto", "-p", "1883")
        time.sleep(1)
        start(sys.executable, str(ROOT / "cloud_node.py"))
        time.sleep(1)
        start(sys.executable, str(ROOT / "edge_node.py"))
        time.sleep(1)
        uav = start(sys.executable, str(ROOT / "uav_node.py"), "--id", "uav_test")

        time.sleep(8)  # let data flow: UAV -> edge -> cloud

        out = tmp_path / "cloud_data.jsonl"
        assert out.exists(), "cloud did not receive any data"
        records = [json.loads(line) for line in out.read_text().splitlines()]
        types = {r["type"] for r in records}
        assert {"telemetry", "sensor"} <= types
        assert all(r["uav_id"] == "uav_test" for r in records)
        assert len(records) >= 10

        # The edge must have sent commands back, so the UAV left "hover" mode
        assert any(r["type"] == "telemetry" and r["mode"] == "move"
                   for r in records), "UAV never received a command"
        assert uav.poll() is None, "UAV process crashed"
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
