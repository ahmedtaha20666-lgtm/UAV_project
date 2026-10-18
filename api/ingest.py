"""Cloud server program (runs as a serverless function, e.g. on Vercel).

The edge server sends its batches here:

    POST /api/ingest        header  X-API-Key: <secret>
                            body    [ {"type": "telemetry", "uav_id": "uav1", "ts": 1.7e9, ...}, ... ]
    GET  /api/ingest        health check

What the server does with a batch
  1. checks the API key              -> 401 if wrong, 500 if the server has no key configured
  2. validates every record          -> 400 if anything is malformed
  3. stores the batch in Firebase    -> 502 if Firebase refuses (the edge keeps the data and retries)

The Firebase login lives only here (environment variables), so the edge server
no longer needs the database password.

Environment variables
  CLOUD_API_KEY       secret the edge must send in X-API-Key
  FIREBASE_DB_URL     https://<project>-default-rtdb.<region>.firebasedatabase.app
  FIREBASE_API_KEY    Web API key
  FIREBASE_EMAIL      the Firebase user this server signs in as
  FIREBASE_PASSWORD   its password

Run it on your own computer instead:  python run_cloud_local.py
"""
import hmac
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from firebase_client import FirebaseClient  # noqa: E402

ROUTES = ("/api/ingest", "/api/ingest/")
MAX_BODY_BYTES = 1_000_000
MAX_RECORDS = 2000
ALLOWED_TYPES = ("telemetry", "sensor")
BAD_KEY_CHARS = re.compile(r"[.$#\[\]/]")      # characters Firebase does not allow in keys

PAUSE_AFTER_LOGIN_FAILURE_S = 60   # do not hammer Firebase Authentication after a failed sign-in

_client = None
_client_cfg = None
_paused_until = 0.0
_last_login_error = ""


def get_client():
    """One Firebase client per server instance (re-used while the instance is warm)."""
    global _client, _client_cfg
    names = ("FIREBASE_DB_URL", "FIREBASE_API_KEY", "FIREBASE_EMAIL", "FIREBASE_PASSWORD")
    cfg = tuple(os.getenv(n, "") for n in names)
    if not all(cfg):
        raise RuntimeError("server is not configured (missing FIREBASE_* variables)")
    if _client is None or cfg != _client_cfg:
        _client, _client_cfg = FirebaseClient(*cfg), cfg
    return _client


def _check_keys(value, depth=0):
    if depth > 4:
        raise ValueError("records are nested too deeply")
    if isinstance(value, dict):
        for k, v in value.items():
            if BAD_KEY_CHARS.search(k) or not k:
                raise ValueError(f"invalid field name: {k!r}")
            _check_keys(v, depth + 1)
    elif isinstance(value, list):
        raise ValueError("lists are not allowed inside records")


def validate_batch(batch):
    """Return the batch if it is valid, otherwise raise ValueError with the reason."""
    if not isinstance(batch, list) or not batch:
        raise ValueError("the body must be a non-empty JSON list of records")
    if len(batch) > MAX_RECORDS:
        raise ValueError(f"too many records (max {MAX_RECORDS})")
    for i, rec in enumerate(batch):
        if not isinstance(rec, dict):
            raise ValueError(f"record {i} is not an object")
        if rec.get("type") not in ALLOWED_TYPES:
            raise ValueError(f"record {i}: type must be one of {ALLOWED_TYPES}")
        uav = rec.get("uav_id")
        if not isinstance(uav, str) or not uav or len(uav) > 64:
            raise ValueError(f"record {i}: uav_id must be a text of 1-64 characters")
        ts = rec.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            raise ValueError(f"record {i}: ts must be a number")
        if len(rec) > 20:
            raise ValueError(f"record {i}: too many fields")
        _check_keys(rec)
    return batch


class handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] not in ROUTES:
            return self._send(404, {"error": "not found"})
        self._send(200, {"status": "ok", "service": "uav-ingest",
                         "api_key_set": bool(os.getenv("CLOUD_API_KEY"))})

    def do_POST(self):
        if self.path.split("?")[0] not in ROUTES:
            return self._send(404, {"error": "not found"})

        # 1) authentication (refuse everything if the server has no key configured)
        secret = os.getenv("CLOUD_API_KEY", "")
        if not secret:
            return self._send(500, {"error": "server is not configured (CLOUD_API_KEY is missing)"})
        sent = self.headers.get("X-API-Key", "")
        if not hmac.compare_digest(sent.encode(), secret.encode()):
            return self._send(401, {"error": "invalid or missing API key"})

        # 2) validation
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0:
            return self._send(400, {"error": "empty body"})
        if length > MAX_BODY_BYTES:
            return self._send(413, {"error": "body too large"})
        try:
            batch = validate_batch(json.loads(self.rfile.read(length)))
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})

        # 3) store in Firebase (but pause for a while after a failed sign-in, so Firebase
        #    Authentication does not block this server for "too many attempts")
        global _paused_until, _last_login_error
        wait = _paused_until - time.time()
        if wait > 0:
            return self._send(502, {"error": f"Firebase sign-in failed recently, pausing {wait:.0f} s "
                                             f"before trying again. Last error: {_last_login_error}"})
        try:
            stored = get_client().send_batch(batch, reload_stats=True)
        except RuntimeError as e:
            if "sign-in failed" in str(e):
                _paused_until = time.time() + PAUSE_AFTER_LOGIN_FAILURE_S
                _last_login_error = str(e)
            code = 500 if "not configured" in str(e) else 502
            print(f"[ingest] {code}: {e}", file=sys.stderr)       # visible in the Vercel logs
            return self._send(code, {"error": str(e)})
        except Exception as e:                      # network problem, timeout, ...
            print(f"[ingest] 502: could not reach Firebase: {e}", file=sys.stderr)
            return self._send(502, {"error": f"could not reach Firebase: {e}"})
        self._send(200, {"stored": stored})

    def log_message(self, *args):
        pass
