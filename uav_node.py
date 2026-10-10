"""UAV node (onboard).

Simulates a drone that:
  * publishes telemetry (position, battery, speed) at TELEMETRY_HZ
  * publishes crowd-density sensor data every SENSOR_PERIOD_S
  * obeys commands from the edge server
  * hovers (fail-safe) if no command arrives for COMMAND_TIMEOUT_S

Run:  python uav_node.py --id uav1
On a real drone you would replace the simulated values with data read
from the flight controller (e.g. via MAVLink / pymavlink).
"""
import argparse
import json
import random
import time

import paho.mqtt.client as mqtt

import config as cfg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default="uav1")
    parser.add_argument("--host", default=cfg.BROKER_HOST)
    args = parser.parse_args()
    uav_id = args.id

    t_tel = cfg.TOPIC_TELEMETRY.format(uav_id=uav_id)
    t_sen = cfg.TOPIC_SENSOR.format(uav_id=uav_id)
    t_cmd = cfg.TOPIC_COMMAND.format(uav_id=uav_id)
    t_sta = cfg.TOPIC_STATUS.format(uav_id=uav_id)

    # --- simulated drone state ---
    state = {"x": 0.0, "y": 0.0, "alt": 30.0, "battery": 100.0,
             "vx": 0.0, "vy": 0.0, "mode": "hover", "last_cmd": time.time()}

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"[{uav_id}] connected to broker ({reason_code})")
        client.subscribe(t_cmd, qos=1)
        client.publish(t_sta, "online", qos=1, retain=True)

    def on_message(client, userdata, msg):
        cmd = json.loads(msg.payload)
        state["last_cmd"] = time.time()
        if cmd["action"] == "move":
            state["vx"], state["vy"] = cmd["vx"], cmd["vy"]
            state["mode"] = "move"
        elif cmd["action"] == "return_home":
            state["mode"] = "return_home"
        else:
            state["vx"] = state["vy"] = 0.0
            state["mode"] = "hover"
        print(f"[{uav_id}] command received: {cmd}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{uav_id}-onboard")
    # Last Will: the broker publishes "offline" if the UAV disconnects unexpectedly
    client.will_set(t_sta, "offline", qos=1, retain=True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, cfg.BROKER_PORT, keepalive=10)
    client.loop_start()

    dt = 1.0 / cfg.TELEMETRY_HZ
    next_sensor = time.time()
    try:
        while True:
            now = time.time()

            # Fail-safe: no command for too long -> hover
            if now - state["last_cmd"] > cfg.COMMAND_TIMEOUT_S and state["mode"] != "hover":
                print(f"[{uav_id}] FAIL-SAFE: no command for {cfg.COMMAND_TIMEOUT_S}s -> hover")
                state["vx"] = state["vy"] = 0.0
                state["mode"] = "hover"

            # Update simulated physics
            if state["mode"] == "return_home":
                state["vx"] = -state["x"] * 0.2
                state["vy"] = -state["y"] * 0.2
            state["x"] += state["vx"] * dt
            state["y"] += state["vy"] * dt
            state["battery"] = max(0.0, state["battery"] - 0.02)

            # 1) Telemetry: small, frequent
            telemetry = {
                "uav_id": uav_id, "ts": round(now, 3),
                "x": round(state["x"], 2), "y": round(state["y"], 2),
                "alt": state["alt"], "battery": round(state["battery"], 1),
                "speed": round((state["vx"] ** 2 + state["vy"] ** 2) ** 0.5, 2),
                "mode": state["mode"],
            }
            client.publish(t_tel, json.dumps(telemetry), qos=0)

            # 2) Sensor data: bigger, less frequent.
            #    Only the *result* of the on-board processing (crowd counts per
            #    zone) is sent, not raw video -> saves bandwidth.
            if now >= next_sensor:
                sensor = {
                    "uav_id": uav_id, "ts": round(now, 3),
                    "crowd": {
                        "north": random.randint(0, 100),
                        "south": random.randint(0, 100),
                        "east":  random.randint(0, 100),
                        "west":  random.randint(0, 100),
                    },
                }
                client.publish(t_sen, json.dumps(sensor), qos=1)
                next_sensor = now + cfg.SENSOR_PERIOD_S

            time.sleep(dt)
    except KeyboardInterrupt:
        print(f"[{uav_id}] stopping")
    finally:
        client.publish(t_sta, "offline", qos=1, retain=True)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
