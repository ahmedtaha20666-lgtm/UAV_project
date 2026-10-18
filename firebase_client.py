"""Tiny Firebase Realtime Database client (standard library only).

The edge server uses it to store batches in Firebase - no server code is needed
in the cloud, so it works on the free Spark plan (no payment method).

How it works
  1. Sign in with an email + password (Firebase Authentication) -> get a token
  2. PATCH the batch into  /records.json?auth=<token>
  3. PATCH the counters into /stats.json?auth=<token>
The database rules only allow *signed-in* users to write, anyone can read.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = os.getenv("FIREBASE_AUTH_URL",
                     "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword")

HINTS = {
    "OPERATION_NOT_ALLOWED": "Enable Email/Password in Authentication -> Sign-in method.",
    "EMAIL_NOT_FOUND": "This user does not exist. Add it in Authentication -> Users.",
    "INVALID_PASSWORD": "Wrong password for this user.",
    "INVALID_LOGIN_CREDENTIALS": "Wrong email or password.",
    "API_KEY_INVALID": "The Web API key is wrong. Copy it from Project settings -> General.",
}


def make_key(record, used):
    """Database key for a record: sortable by time and the same on a retry.

    Example: 1791640415354_uav1_sensor
    """
    uav = re.sub(r"[.$#\[\]/]", "_", str(record.get("uav_id", "uav")))
    key = f"{int(float(record.get('ts', time.time())) * 1000):013d}_{uav}_{record.get('type', 'x')}"
    base, n = key, 1
    while key in used:           # two identical keys inside one batch
        n += 1
        key = f"{base}_{n}"
    used.add(key)
    return key


class FirebaseClient:
    def __init__(self, db_url, api_key, email, password, timeout=30):
        self.db_url = db_url.rstrip("/")
        self.api_key = api_key
        self.email = email
        self.password = password
        self.timeout = timeout
        self._token = None
        self._expires = 0.0
        self._stats = None

    # ---------------------------------------------------------- HTTP helpers
    def _request(self, method, url, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read() or b"null")

    def _sign_in(self):
        url = f"{AUTH_URL}?key={urllib.parse.quote(self.api_key)}"
        try:
            out = self._request("POST", url, {"email": self.email, "password": self.password,
                                              "returnSecureToken": True})
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read())["error"]["message"]
            except Exception:
                message = f"HTTP {e.code}"
            code = message.split(" ")[0]
            raise RuntimeError(f"Firebase sign-in failed: {message}. {HINTS.get(code, '')}") from None
        self._token = out["idToken"]
        self._expires = time.time() + int(out.get("expiresIn", 3600)) - 120

    def _db(self, method, path, body=None, retry=True):
        if not self._token or time.time() >= self._expires:
            self._sign_in()
        url = f"{self.db_url}/{path}.json?auth={urllib.parse.quote(self._token)}"
        try:
            return self._request(method, url, body)
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry:      # token expired -> sign in again, once
                self._token = None
                return self._db(method, path, body, retry=False)
            detail = e.read().decode(errors="replace")[:200]
            raise RuntimeError(f"Database {method} /{path} failed (HTTP {e.code}): {detail}. "
                               "Check the Realtime Database Rules.") from None

    # ------------------------------------------------------------------ API
    def send_batch(self, batch, reload_stats=False):
        """Store a list of records. Raises on any error (the caller then retries).

        reload_stats=True re-reads the counters from the database every time.
        A serverless server must use it, because several instances can run at once.
        """
        if self._stats is None or reload_stats:
            old = self._db("GET", "stats") or {}
            self._stats = {k: int(old.get(k, 0)) for k in ("total", "batches", "telemetry", "sensor")}

        used = set()
        self._db("PATCH", "records", {make_key(r, used): r for r in batch})

        new = dict(self._stats)
        new["total"] += len(batch)
        new["batches"] += 1
        new["telemetry"] += sum(1 for r in batch if r.get("type") == "telemetry")
        new["sensor"] += sum(1 for r in batch if r.get("type") == "sensor")
        new["last_update"] = int(time.time() * 1000)
        self._db("PATCH", "stats", new)
        self._stats = {k: new[k] for k in ("total", "batches", "telemetry", "sensor")}
        return len(batch)
