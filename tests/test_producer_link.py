"""Producer -> edge connection reuse, against a real local HTTP server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import producer


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive capable, like uvicorn
    close_after_each = False
    peers: set = set()

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        _Handler.peers.add(self.client_address)
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self.close_after_each:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.peers = set()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    for key in list(producer._conns):
        producer._close_conn(key)


def _url(srv, path="/images/summary"):
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


def test_one_connection_for_many_messages(server, monkeypatch):
    monkeypatch.setenv("PERSISTENT", "1")
    _Handler.close_after_each = False
    before = producer.CONNECTS["count"]
    for _ in range(5):
        status, doc = producer._post(_url(server), b"{}", timeout=5)
        assert status == 200 and doc == {"ok": True}
    assert producer.CONNECTS["count"] - before == 1
    assert len(_Handler.peers) == 1  # one client port = one TCP connection


def test_server_closing_the_connection_is_not_a_link_failure(server, monkeypatch):
    monkeypatch.setenv("PERSISTENT", "1")
    _Handler.close_after_each = True
    for _ in range(3):
        status, _doc = producer._post(_url(server), b"{}", timeout=5)
        assert status == 200


def test_connection_per_message_when_disabled(server, monkeypatch):
    monkeypatch.setenv("PERSISTENT", "0")
    _Handler.close_after_each = False
    before = producer.CONNECTS["count"]
    for _ in range(3):
        producer._post(_url(server), b"{}", timeout=5)
    assert producer.CONNECTS["count"] - before == 3


def test_unreachable_edge_raises(monkeypatch):
    monkeypatch.setenv("PERSISTENT", "1")
    with pytest.raises(OSError):
        producer._post("http://127.0.0.1:1/images/summary", b"{}", timeout=1)
