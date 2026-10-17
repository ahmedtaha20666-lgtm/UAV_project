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

## Real cloud with Firebase (free Spark plan, no payment method)

The free plan has no server code (Cloud Functions need a paid plan), so the cloud is
**Firebase Realtime Database + Firebase Hosting**:

```
Edge (edge_node.py) --HTTPS, signed in--> Firebase Realtime Database <--reads-- Dashboard (Firebase Hosting)
```

- `firebase_client.py` signs in with an email/password and writes each batch with the database REST API.
- `database.rules.json`: anyone can read, only the signed-in edge user can write.
- `public/index.html` is the dashboard (public URL `https://<project-id>.web.app`).
- GitHub Actions: lint -> tests -> deploy the dashboard to Firebase Hosting -> check it answers.

Run the edge against Firebase (Windows cmd):

    set FIREBASE_DB_URL=https://<project>-default-rtdb.<region>.firebasedatabase.app
    set FIREBASE_API_KEY=<Web API key>
    set FIREBASE_EMAIL=edge@uav.local
    set FIREBASE_PASSWORD=<password>
    python edge_node.py

GitHub secrets needed: `FIREBASE_SERVICE_ACCOUNT` (JSON key), `FIREBASE_PROJECT_ID`, `FIREBASE_DB_URL`.

Without the `FIREBASE_*` variables the edge sends to `cloud_node.py` over HTTP (local mode).

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
