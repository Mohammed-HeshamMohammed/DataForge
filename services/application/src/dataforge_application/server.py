"""Process entry point.

    python -m dataforge_application.server              JSON lines over stdin/stdout (desktop host sidecar)
    python -m dataforge_application.server --http 8765  local HTTP bridge for browser-only UI development
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import REPO_ROOT, Service
from .logs import log


def serve_stdio(service: Service) -> None:
    protocol_out = sys.stdout
    sys.stdout = sys.stderr  # nothing but protocol lines may reach the host's stdout
    for line in sys.stdin:
        line = line.lstrip("﻿")  # some hosts prefix the first write with a UTF-8 byte order mark
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = {"schema_version": 1, "ok": False, "error": {"code": "invalid_json", "message": "Request is not valid JSON"}}
        else:
            response = {"id": request.get("id"), **service.handle(request)}
        protocol_out.write(json.dumps(response, default=str, ensure_ascii=False) + "\n")
        protocol_out.flush()


def serve_http(service: Service, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/rpc":
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                response = service.handle(json.loads(body))
            except json.JSONDecodeError:
                response = {"schema_version": 1, "ok": False, "error": {"code": "invalid_json", "message": "Request is not valid JSON"}}
            payload = json.dumps(response, default=str, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            return

    # Loopback only: this bridge has no authentication and must never be exposed to a network.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    log("info", "http_bridge.listening", port=port)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", type=int, help="serve the command API on 127.0.0.1:<port>")
    parser.add_argument("--resources", type=Path, default=REPO_ROOT, help="directory containing migrations/ and packages/presets/")
    args = parser.parse_args()
    service = Service(args.resources / "migrations", args.resources / "packages" / "presets")
    service.open_recent()
    log("info", "service.started", mode="http" if args.http else "stdio")
    if args.http:
        serve_http(service, args.http)
    else:
        serve_stdio(service)


if __name__ == "__main__":
    main()
