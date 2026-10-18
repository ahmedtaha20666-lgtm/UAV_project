"""Edge server node.

Responsibilities:
  1. Receive telemetry + sensor data from every UAV (MQTT subscribe)
  2. Run the decision logic (here: a simple rule; replace with the DQN/MADDPG model)
  3. Send commands back to the UAV (MQTT publish)
  4. Buffer everything and forward it to the cloud in batches (HTTP POST)

Run:  python edge_node.py
(The MQTT broker - Mosquitto - runs on the same machine.)
"""
import json
import threading
import time
import urllib.error
import urllib.request

import paho.mqtt.client as mqtt

import config as cfg
from firebase_client import FirebaseClient

# Latest telemetry per UAV, and the buffer waiting to be sent to the cloud
latest = {}
buffer = []
buffer_lock = threading.Lock()
MAX_BATCH = 500        # records per upload (the cloud server accepts at most 2000)
MAX_BUFFER = 20000     # never keep more than this many unsent records (oldest are dropped)
MAX_BACKOFF_S = 300    # wait at most 5 minutes between attempts while the cloud is failing

# Direction vectors for each zone of the stadium (x, y)
ZONE_DIRECTION = {"north": (0, 1), "south": (0, -1), "east": (1, 0), "west": (-1, 0)}


def decide(uav_id, sensor):
    """Decision logic.

    PLACEHOLDER for the AI model: fly toward the zone with the most people.
    Replace this function with `model.predict(state)` from your DQN/MADDPG
    teammates - the rest of the pipeline stays the same.
    """
    crowd = sensor["crowd"]
    target = max(crowd, key=crowd.get)
    vx, vy = ZONE_DIRECTION[target]
    battery = latest.get(uav_id, {}).get("battery", 100)
    if battery < 20:
        return {"action": "return_home", "reason": "low battery"}
    return {"action": "move", "vx": vx, "vy": vy, "reason": f"most crowded: {target}"}


def on_connect(client, userdata, flags, reason_code, properties):
    print(f"[edge] connected to broker ({reason_code})")
    client.subscribe("uav/+/telemetry", qos=0)
    client.subscribe("uav/+/sensor", qos=1)
    client.subscribe("uav/+/status", qos=1)


def on_message(client, userdata, msg):
    parts = msg.topic.split("/")          # uav / <id> / <kind>
    uav_id, kind = parts[1], parts[2]

    if kind == "status":
        print(f"[edge] {uav_id} is {msg.payload.decode()}")
        return

    data = json.loads(msg.payload)

    if kind == "telemetry":
        latest[uav_id] = data
        add_to_buffer({"type": "telemetry", **data})

    elif kind == "sensor":
        add_to_buffer({"type": "sensor", **data})
        command = decide(uav_id, data)
        client.publish(cfg.TOPIC_COMMAND.format(uav_id=uav_id), json.dumps(command), qos=1)
        print(f"[edge] {uav_id} sensor={data['crowd']} -> command={command}")


_firebase = None


def send_to_cloud(batch):
    """Send one batch to the cloud. Raises an exception if it fails."""
    global _firebase
    if cfg.FIREBASE_DB_URL:                       # Firebase backend (free, no server)
        if _firebase is None:
            _firebase = FirebaseClient(cfg.FIREBASE_DB_URL, cfg.FIREBASE_API_KEY,
                                       cfg.FIREBASE_EMAIL, cfg.FIREBASE_PASSWORD)
        _firebase.send_batch(batch)
        return "Firebase"
    req = urllib.request.Request(                 # plain HTTP cloud server (cloud_node.py)
        cfg.CLOUD_URL, data=json.dumps(batch).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": cfg.CLOUD_API_KEY},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:           # show WHY the server refused
        try:
            reason = json.loads(e.read()).get("error", "")
        except Exception:
            reason = ""
        raise RuntimeError(f"HTTP {e.code}: {reason or e.reason}") from None


def add_to_buffer(record):
    with buffer_lock:
        buffer.append(record)
        if len(buffer) > MAX_BUFFER:
            del buffer[:len(buffer) - MAX_BUFFER]


def next_delay(failures):
    """Seconds to wait before the next upload: normal rate, then slower after each failure."""
    if failures == 0:
        return cfg.CLOUD_BATCH_PERIOD_S
    return min(cfg.CLOUD_BATCH_PERIOD_S * 2 ** failures, MAX_BACKOFF_S)


def flush_buffer():
    """Send everything in the buffer in chunks. Raises on error; unsent records stay in the buffer."""
    sent = 0
    while True:
        with buffer_lock:
            batch = buffer[:MAX_BATCH]
        if not batch:
            return sent
        how = send_to_cloud(batch)
        with buffer_lock:
            del buffer[:len(batch)]               # remove only after the cloud accepted them
        sent += len(batch)
        print(f"[edge] sent {len(batch)} records to cloud ({how})")


def forward_to_cloud():
    """Upload the buffer regularly. After an error, wait longer each time (back-off)."""
    failures = 0
    while True:
        time.sleep(next_delay(failures))
        try:
            flush_buffer()
            failures = 0
        except Exception as e:
            failures += 1
            print(f"[edge] cloud error ({e}); keeping {len(buffer)} records, "
                  f"next try in {next_delay(failures):.0f} s")


def main():
    threading.Thread(target=forward_to_cloud, daemon=True).start()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edge-server")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(cfg.BROKER_HOST, cfg.BROKER_PORT, keepalive=30)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("[edge] stopping")


if __name__ == "__main__":
    main()
