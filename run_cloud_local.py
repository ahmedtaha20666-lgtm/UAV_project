"""Run the cloud server program on your own computer (same code that runs on Vercel).

    python run_cloud_local.py

Set the same environment variables as on Vercel first (CLOUD_API_KEY and FIREBASE_*),
then point the edge at it:   set CLOUD_URL=http://localhost:8000/api/ingest
"""
import os
from http.server import ThreadingHTTPServer

from api.ingest import handler

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"[cloud] listening on http://localhost:{port}/api/ingest")
    ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()
