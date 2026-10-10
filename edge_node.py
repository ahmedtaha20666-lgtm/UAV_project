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
import urllib.request

import paho.mqtt.client as mqtt

import config as cfg

# Latest telemetry per UAV, and the buffer waiting to be sent to the cloud
latest = {}
buffer = []
buffer_lock = threading.Lock()

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
        with buffer_lock:
            buffer.append({"type": "telemetry", **data})

    elif kind == "sensor":
        with buffer_lock:
            buffer.append({"type": "sensor", **data})
        command = decide(uav_id, data)
        client.publish(cfg.TOPIC_COMMAND.format(uav_id=uav_id), json.dumps(command), qos=1)
        print(f"[edge] {uav_id} sensor={data['crowd']} -> command={command}")


def forward_to_cloud():
    """Every CLOUD_BATCH_PERIOD_S seconds, send buffered data to the cloud."""
    while True:
        time.sleep(cfg.CLOUD_BATCH_PERIOD_S)
        with buffer_lock:
            batch, buffer[:] = list(buffer), []
        if not batch:
            continue
        try:
            req = urllib.request.Request(
                cfg.CLOUD_URL, data=json.dumps(batch).encode(),
                headers={"Content-Type": "application/json",
                         "X-API-Key": cfg.CLOUD_API_KEY}, method="POST")
            # long timeout: a free hosting service needs ~1 min to wake up
            with urllib.request.urlopen(req, timeout=90) as resp:
                print(f"[edge] sent {len(batch)} records to cloud (HTTP {resp.status})")
        except Exception as e:
            # Cloud unreachable: keep the data and retry in the next round
            print(f"[edge] cloud unreachable ({e}); keeping {len(batch)} records")
            with buffer_lock:
                buffer[:0] = batch


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
