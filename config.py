"""Shared settings for the UAV -> Edge -> Cloud pipeline."""
import os

# --- MQTT broker (runs on the edge server) ---
BROKER_HOST = os.getenv("BROKER_HOST", "localhost")  # edge server IP on real devices
BROKER_PORT = 1883

# --- Topics ---
# UAV -> Edge
TOPIC_TELEMETRY = "uav/{uav_id}/telemetry"   # position, battery, speed  (small, frequent)
TOPIC_SENSOR = "uav/{uav_id}/sensor"      # crowd density from camera (bigger, slower)
TOPIC_STATUS = "uav/{uav_id}/status"      # online/offline (Last Will)
# Edge -> UAV
TOPIC_COMMAND = "uav/{uav_id}/command"     # move / hover / return_home

# --- Rates ---
TELEMETRY_HZ = 2          # telemetry messages per second
SENSOR_PERIOD_S = float(os.getenv("SENSOR_PERIOD_S", "2.0"))  # one sensor message every N s

# --- Fail-safe ---
COMMAND_TIMEOUT_S = 5.0   # UAV hovers if no command arrives for this long

# --- Edge -> Cloud ---
CLOUD_URL = os.getenv("CLOUD_URL", "http://localhost:8000/ingest")
CLOUD_API_KEY = os.getenv("CLOUD_API_KEY", "")   # secret shared with the cloud server
CLOUD_BATCH_PERIOD_S = float(os.getenv("CLOUD_BATCH_PERIOD_S", "10"))  # seconds
