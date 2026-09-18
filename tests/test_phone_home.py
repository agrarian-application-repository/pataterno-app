"""_phone_home: credential-free, guarded by PHONE_HOME_URL, never raises.

No network happens in these tests - urlopen is monkeypatched.
"""

import json

import app


def test_phone_home_is_a_noop_when_url_is_unset(monkeypatch):
    monkeypatch.delenv("PHONE_HOME_URL", raising=False)

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("phone-home must open no connection when disabled")

    monkeypatch.setattr(app.urllib.request, "urlopen", _must_not_be_called)
    app._phone_home(json.dumps({"verdict": "disabled"}))  # must simply return


def test_phone_home_posts_the_payload_with_the_token(monkeypatch):
    monkeypatch.setenv("PHONE_HOME_URL", "http://127.0.0.1:1/beacon")
    monkeypatch.setenv("PHONE_HOME_TOKEN", "shhh")
    monkeypatch.setenv("PHONE_HOME_ATTEMPTS", "1")
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fake_urlopen(request, timeout=5):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        # urllib title-cases header keys: "X-Token" is stored as "X-token".
        captured["token"] = request.get_header("X-token")
        captured["body"] = request.data
        return _Resp()

    monkeypatch.setattr(app.urllib.request, "urlopen", _fake_urlopen)
    app._phone_home(json.dumps({"verdict": "no_credentials"}))

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:1/beacon"
    assert captured["token"] == "shhh"
    assert b"no_credentials" in captured["body"]


def test_phone_home_never_raises_when_the_listener_is_down(monkeypatch):
    monkeypatch.setenv("PHONE_HOME_URL", "http://127.0.0.1:1/beacon")
    monkeypatch.setenv("PHONE_HOME_ATTEMPTS", "2")
    monkeypatch.setenv("PHONE_HOME_RETRY_DELAY", "0")

    def _refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(app.urllib.request, "urlopen", _refuse)
    app._phone_home(json.dumps({"verdict": "x"}))  # must swallow and return


def test_phone_home_sends_no_token_header_when_unset(monkeypatch):
    monkeypatch.setenv("PHONE_HOME_URL", "http://127.0.0.1:1/beacon")
    monkeypatch.delenv("PHONE_HOME_TOKEN", raising=False)
    monkeypatch.setenv("PHONE_HOME_ATTEMPTS", "1")
    seen = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fake_urlopen(request, timeout=5):
        seen["token"] = request.get_header("X-token")
        return _Resp()

    monkeypatch.setattr(app.urllib.request, "urlopen", _fake_urlopen)
    app._phone_home(json.dumps({"verdict": "disabled"}))
    assert seen["token"] is None
