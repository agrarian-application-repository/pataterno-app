#!/usr/bin/env python3
"""
Phone-home listener for the PATATERNO reachability test.

The container, running wherever it happens to run (a Testbed 2 node, the
portal, a laptop), sends its /dbcheck diagnostic here. This lets us read the
verdict without a shell on that machine and without a credential anywhere - the
diagnostic in its default (credential-free) mode is a pure TCP reachability
result: hostname, egress IP, and whether the AGRARIAN database answered.

This program only ever RECEIVES and PRINTS text. It never executes anything it
is sent, and it ignores any request that does not carry the shared token, so a
random internet scanner hitting the open port cannot pollute the log.

Usage:
    python tools/phone_home_listener.py --port 48080 --token <shared-secret>

Then point the container at it:
    PHONE_HOME_URL=http://<your-wan-ip>:48080/beacon
    PHONE_HOME_TOKEN=<shared-secret>
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = ""  # set from argv in main()
LOG_PATH = ""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _record(line: str) -> None:
    print(line, flush=True)
    if LOG_PATH:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    # Silence the default per-request stderr logging; we print our own lines.
    def log_message(self, *args) -> None:  # noqa: D401
        return

    def _token_ok(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        supplied = (query.get("token", [""])[0]) or self.headers.get("X-Token", "")
        return bool(TOKEN) and supplied == TOKEN

    def do_GET(self) -> None:
        # A bare health check, so you can confirm the listener is up.
        if urlparse(self.path).path == "/health":
            self._reply(200, {"status": "listening"})
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        client = self.client_address[0]
        if not self._token_ok():
            # Do not log the body of unauthenticated noise; just note it.
            _record(f"[{_now()}] {client}  IGNORED (bad/missing token)")
            self._reply(403, {"error": "forbidden"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(min(length, 65536)) if length else b""
        text = raw.decode("utf-8", "replace")

        _record(f"[{_now()}] BEACON from {client}")
        try:
            doc = json.loads(text)
            verdict = doc.get("verdict", "?")
            tcp = doc.get("tcp", {})
            container = doc.get("container", {})
            _record(
                f"    verdict={verdict}  tcp.reachable={tcp.get('reachable')} "
                f"ms={tcp.get('ms')} kind={tcp.get('failure_kind')}"
            )
            _record(
                f"    egress_ip={container.get('egress_ip')} "
                f"image_tag={container.get('image_tag')} "
                f"server_sees={doc.get('auth', {}).get('client_addr_seen_by_server')}"
            )
            _record("    " + json.dumps(doc, separators=(",", ":")))
        except (ValueError, AttributeError):
            # Not JSON - print it verbatim, it is just debug text.
            _record("    " + text.strip()[:2000])

        self._reply(200, {"received": True})

    def _reply(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> int:
    global TOKEN, LOG_PATH
    parser = argparse.ArgumentParser(description="PATATERNO phone-home listener")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=48080)
    parser.add_argument("--token", required=True, help="shared secret; requests without it are ignored")
    parser.add_argument("--log", default="phone_home.log", help="append received beacons here")
    args = parser.parse_args()

    TOKEN = args.token
    LOG_PATH = args.log

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    _record(f"[{_now()}] listening on {args.host}:{args.port}  (token required)")
    _record(f"[{_now()}] point the container at  http://<this-host>:{args.port}/beacon")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _record(f"[{_now()}] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
