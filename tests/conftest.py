import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import firebase_client  # noqa: E402
from fake_firebase import FakeFirebase  # noqa: E402


@pytest.fixture()
def fake():
    """Start the fake Firebase on a free port and point the client's sign-in at it."""
    FakeFirebase.db, FakeFirebase.tokens, FakeFirebase.sign_ins = {}, set(), 0
    srv = HTTPServer(("127.0.0.1", 0), FakeFirebase)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    old = firebase_client.AUTH_URL
    firebase_client.AUTH_URL = base + "/auth"
    yield base
    firebase_client.AUTH_URL = old
    srv.shutdown()
