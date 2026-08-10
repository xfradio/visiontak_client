"""Request-level behaviour, stubbed at urlopen.

The VisionTAK server is a Next.js app with a catch-all route, so a wrong path comes
back as HTTP 200 with the SPA shell. Treating that as success would leave the kiosk
silently showing nothing, so the client checks the content type.
"""

import io
import urllib.error

import pytest

from visiontak_client.api import ApiError, AuthError, VisionTakClient
from visiontak_client.config import Config

TOKEN = "test-token"


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str) -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


@pytest.fixture
def client():
    return VisionTakClient(
        Config(server_url="http://localhost:3001", api_token=TOKEN, device_id="test")
    )


def stub(monkeypatch, response):
    captured = {}

    def fake_urlopen(request, **_kwargs):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


def test_bearer_token_is_sent(client, monkeypatch):
    captured = stub(monkeypatch, FakeResponse(b'{"allowedDashboards": []}', "application/json"))
    client.fetch_client_config()
    assert captured["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert captured["url"] == "http://localhost:3001/api/v1/client/config"


def test_no_authorization_header_when_no_token(monkeypatch):
    client = VisionTakClient(Config(server_url="http://localhost:3001", device_id="test"))
    captured = stub(monkeypatch, FakeResponse(b'{"dashboards": []}', "application/json"))
    client.fetch_client_config()
    assert "Authorization" not in captured["headers"]


def test_html_response_is_rejected_rather_than_parsed(client, monkeypatch):
    """A 200 carrying the SPA shell means the path is wrong, not that all is well."""
    stub(monkeypatch, FakeResponse(b"<!DOCTYPE html><title>Sign in</title>", "text/html"))
    with pytest.raises(ApiError, match="not JSON"):
        client.fetch_client_config()


@pytest.mark.parametrize("code", [401, 403])
def test_rejected_token_raises_auth_error(client, monkeypatch, code):
    stub(monkeypatch, urllib.error.HTTPError("u", code, "Unauthorized", {}, None))
    with pytest.raises(AuthError, match="api-token rejected"):
        client.fetch_client_config()


def test_auth_error_is_distinguishable_from_a_transient_failure(client, monkeypatch):
    stub(monkeypatch, urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None))
    with pytest.raises(ApiError) as exc_info:
        client.fetch_client_config()
    assert not isinstance(exc_info.value, AuthError)


def test_connection_refused_is_an_api_error(client, monkeypatch):
    stub(monkeypatch, urllib.error.URLError("connection refused"))
    with pytest.raises(ApiError, match="connection refused"):
        client.fetch_client_config()


def test_unset_server_url_is_reported_clearly():
    client = VisionTakClient(Config(server_url="", device_id="test"))
    with pytest.raises(ApiError, match="server-url is not configured"):
        client.fetch_client_config()


def test_malformed_json_is_an_api_error(client, monkeypatch):
    stub(monkeypatch, FakeResponse(b"{not json", "application/json"))
    with pytest.raises(ApiError, match="non-JSON"):
        client.fetch_client_config()
