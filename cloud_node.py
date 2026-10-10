"""Cloud node - a small web server that can run on the internet (e.g. Render).

Endpoints
  POST /ingest   edge server sends a batch of records here (needs API key if set)
  GET  /         web dashboard (counts + latest records) - great for demos
  GET  /data     latest records as JSON
  GET  /health   returns "ok" (used by GitHub Actions to check the deploy)

Settings (environment variables)
  PORT           port to listen on (hosting services set this automatically)
  CLOUD_API_KEY  secret key the edge must send in the X-API-Key header.
                 If empty, no key is required (fine for local testing).

Run locally:  python cloud_node.py
"""
import html
import json
import os
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

OUTPUT_FILE = os.getenv("CLOUD_OUTPUT_FILE", "cloud_data.jsonl")
MAX_IN_MEMORY = 5000

records = deque(maxlen=MAX_IN_MEMORY)   # latest records kept in memory
stats = {"total": 0, "batches": 0}
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------- POST
    def do_POST(self):
        if urlparse(self.path).path != "/ingest":
            return self._send(404, '{"error": "not found"}')

        key = os.getenv("CLOUD_API_KEY", "")
        if key and self.headers.get("X-API-Key") != key:
            return self._send(401, '{"error": "invalid or missing API key"}')

        try:
            length = int(self.headers.get("Content-Length", 0))
            batch = json.loads(self.rfile.read(length))
            if not isinstance(batch, list):
                raise ValueError("expected a list of records")
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, json.dumps({"error": str(e)}))

        with lock:
            records.extend(batch)
            stats["total"] += len(batch)
            stats["batches"] += 1
        try:  # the file is only a bonus: hosting disks may be temporary
            with open(OUTPUT_FILE, "a") as f:
                for r in batch:
                    f.write(json.dumps(r) + "\n")
        except OSError:
            pass
        print(f"[cloud] stored {len(batch)} records (total {stats['total']})")
        self._send(200, json.dumps({"stored": len(batch)}))

    # -------------------------------------------------------------- GET
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, "ok", "text/plain")

        if url.path == "/data":
            limit = int(parse_qs(url.query).get("limit", ["50"])[0])
            with lock:
                latest = list(records)[-limit:]
                body = {"total_received": stats["total"],
                        "batches": stats["batches"], "records": latest}
            return self._send(200, json.dumps(body))

        if url.path == "/":
            return self._send(200, self._dashboard(), "text/html; charset=utf-8")

        self._send(404, '{"error": "not found"}')

    def _dashboard(self):
        with lock:
            latest = list(records)[-20:][::-1]
            total, batches = stats["total"], stats["batches"]
            n_tel = sum(1 for r in records if r.get("type") == "telemetry")
            n_sen = sum(1 for r in records if r.get("type") == "sensor")
        rows = "".join(
            f"<tr><td>{html.escape(str(r.get('type')))}</td>"
            f"<td>{html.escape(str(r.get('uav_id')))}</td>"
            f"<td><code>{html.escape(json.dumps({k: v for k, v in r.items() if k not in ('type', 'uav_id')}))}"
            f"</code></td></tr>" for r in latest)
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5"><title>UAV Cloud</title>
<style>body{{font-family:sans-serif;margin:2rem}}td,th{{border:1px solid #ccc;padding:4px 8px;
text-align:left}}table{{border-collapse:collapse}}</style></head><body>
<h1>UAV Cloud Server</h1>
<p><b>Total records received:</b> {total} &nbsp; <b>Batches:</b> {batches}
&nbsp; <b>Telemetry:</b> {n_tel} &nbsp; <b>Sensor:</b> {n_sen}</p>
<p>Latest 20 records (page refreshes every 5 s):</p>
<table><tr><th>type</th><th>uav</th><th>data</th></tr>{rows}</table>
</body></html>"""

    def log_message(self, *args):  # silence default logging
        pass


def make_server(port):
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"[cloud] listening on port {port}")
    make_server(port).serve_forever()
