import httpx
import pytest

from app.utils import retry_on_transient_error


def _response(status_code: int) -> httpx.Response:
    request = httpx.Request("GET", "https://example.com")
    return httpx.Response(status_code=status_code, request=request)


def test_retries_transient_transport_error_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    @retry_on_transient_error(retries=2)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_gives_up_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    @retry_on_transient_error(retries=2)
    def always_fails():
        calls["n"] += 1
        raise httpx.ConnectTimeout("boom")

    with pytest.raises(httpx.ConnectTimeout):
        always_fails()
    assert calls["n"] == 3  # initial attempt + 2 retries


def test_does_not_retry_client_errors(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    @retry_on_transient_error(retries=2)
    def bad_request():
        calls["n"] += 1
        raise httpx.HTTPStatusError("bad", request=httpx.Request("GET", "https://x"), response=_response(404))

    with pytest.raises(httpx.HTTPStatusError):
        bad_request()
    assert calls["n"] == 1  # no retries on a 4xx


def test_retries_server_errors(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    @retry_on_transient_error(retries=1)
    def server_error_then_ok():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.HTTPStatusError("boom", request=httpx.Request("GET", "https://x"), response=_response(503))
        return "recovered"

    assert server_error_then_ok() == "recovered"
    assert calls["n"] == 2
