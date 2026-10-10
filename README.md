# UAV → Edge → Cloud Data Pipeline (Prototype)

```
 ┌──────────┐  telemetry (MQTT, 2 Hz)   ┌─────────────────┐  batched JSON (HTTP, 10 s)  ┌───────┐
 │   UAV    │ ───────────────────────►  │   Edge server   │ ──────────────────────────► │ Cloud │
 │ uav_node │  sensor/crowd (MQTT, 0.5Hz)│ Mosquitto broker│                             │ cloud │
 │          │ ◄───────────────────────  │ + edge_node.py  │                             │ _node │
 └──────────┘  commands (MQTT)          └─────────────────┘                             └───────┘
```

## Setup

```bash
pip install -r requirements.txt
# Install the MQTT broker
#   Ubuntu/Debian:  sudo apt install mosquitto
#   Windows:        download from https://mosquitto.org/download/
#   macOS:          brew install mosquitto
```

## Run (4 terminals)

```bash
mosquitto -p 1883          # 1) broker (edge server)
python cloud_node.py       # 2) cloud
python edge_node.py        # 3) edge server
python uav_node.py --id uav1   # 4) UAV   (add --id uav2 for a second UAV)
```

Received data is stored in `cloud_data.jsonl`.

## MQTT topics

| Topic | Direction | Content | QoS |
|---|---|---|---|
| `uav/<id>/telemetry` | UAV → Edge | position, battery, speed, mode | 0 (fast, loss OK) |
| `uav/<id>/sensor` | UAV → Edge | crowd count per zone | 1 (must arrive) |
| `uav/<id>/command` | Edge → UAV | move / hover / return_home | 1 |
| `uav/<id>/status` | UAV → Edge | online / offline (Last Will) | 1, retained |

## Design decisions (for your report)

- **MQTT between UAV and Edge**: lightweight publish/subscribe, low overhead, works well on weak links.
- **QoS 0 for telemetry, QoS 1 for sensor data and commands**: telemetry is frequent and a lost sample is replaced by the next one; commands must not be lost.
- **Only processed results leave the UAV** (crowd counts, not raw video) to save bandwidth and battery.
- **Edge makes the real-time decision** (`decide()` in `edge_node.py`); this is where the AI model plugs in.
- **Batched HTTP to the cloud**: the cloud is not time-critical, so data is sent every 10 s. If the cloud is unreachable the edge keeps the data and retries.
- **Fail-safe**: if the UAV gets no command for 5 s it hovers. A Last Will message tells the edge when the UAV drops.

## Connecting your teammates' work

- **AI team**: replace `decide()` in `edge_node.py` with the DQN/MADDPG model's prediction.
- **Hardware**: run `uav_node.py` on the Raspberry Pi; replace the simulated values with real flight-controller data (pymavlink).
- **Real devices**: set `BROKER_HOST` in `config.py` to the edge server's IP address.
- **Simulation team**: have the simulator publish to the same topics instead of `uav_node.py`.

## Automated testing with GitHub Actions

Every push / pull request to `main` triggers `.github/workflows/ci.yml`, which:

1. **lint** - checks code style with flake8
2. **test** - installs Mosquitto, then runs `pytest`:
   - unit tests for the decision logic
   - an integration test that starts broker + cloud + edge + UAV and checks that
     telemetry/sensor data reach the cloud and that the edge's commands reach the UAV

Run the same checks locally:

```bash
pip install pytest flake8
flake8 . --max-line-length=120
pytest -v
```

## Real cloud server (Render) + automatic deploy

- `cloud_node.py` is a web server. Hosted on Render it gets a public URL such as
  `https://uav-cloud.onrender.com` with a dashboard at `/`, JSON at `/data`, health check at `/health`.
- The edge sends data to it over HTTPS with a secret `X-API-Key`.
- On every push to `main`, GitHub Actions runs lint -> tests -> **deploy** (calls Render's deploy hook)
  -> checks that `/health` answers.

Render settings: Build `pip install -r requirements.txt`, Start `python cloud_node.py`,
env var `CLOUD_API_KEY=<your secret>`, Auto-Deploy **Off**.
GitHub secrets: `RENDER_DEPLOY_HOOK_URL`, `CLOUD_BASE_URL` (e.g. `https://uav-cloud.onrender.com`).

Run the edge against the live cloud (Windows cmd):

    set CLOUD_URL=https://uav-cloud.onrender.com/ingest
    set CLOUD_API_KEY=<your secret>
    python edge_node.py

Note: Render's free plan sleeps after 15 min idle (first request takes ~1 min) and its disk is temporary,
so the live data is kept in memory and shown on the dashboard.
