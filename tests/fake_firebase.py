"""A fake local Firebase (sign-in + Realtime Database with write rules) for the tests."""
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

API_KEY, EMAIL, PASSWORD = "test-key", "edge@uav.local", "s3cret"


class FakeFirebase(BaseHTTPRequestHandler):
    db = {}          # the fake database  {"records": {...}, "stats": {...}}
    tokens = set()
    sign_ins = 0

    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))

    def do_POST(self):                      # sign in
        url = urlparse(self.path)
        body = self._body()
        if parse_qs(url.query).get("key") != [API_KEY]:
            return self._reply(400, {"error": {"message": "API_KEY_INVALID"}})
        if body["email"] != EMAIL or body["password"] != PASSWORD:
            return self._reply(400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}})
        FakeFirebase.sign_ins += 1
        token = f"token{FakeFirebase.sign_ins}"
        FakeFirebase.tokens.add(token)
        self._reply(200, {"idToken": token, "expiresIn": "3600"})

    def _db(self, method):
        url = urlparse(self.path)
        node = url.path.strip("/").removesuffix(".json")
        token = parse_qs(url.query).get("auth", [None])[0]
        if method == "PATCH" and token not in FakeFirebase.tokens:    # rules: write needs auth
            return self._reply(401, {"error": "Permission denied"})
        if method == "GET":
            return self._reply(200, FakeFirebase.db.get(node))
        FakeFirebase.db.setdefault(node, {}).update(self._body())
        self._reply(200, {})

    def do_GET(self):
        self._db("GET")

    def do_PATCH(self):
        self._db("PATCH")

    def log_message(self, *args):
        pass
