import json
from unittest import mock

import pytest

from visiontak_client.api import REGISTER_PATH, ApiError, VisionTakClient
from visiontak_client.config import Config, ensure_device_id


def _client(**overrides):
    base = {"server_url": "http://vt.example", "device_id": "b0f1c2d3-4444-5555-6666-777788889999"}
    base.update(overrides)
    return VisionTakClient(Config(**base))


def _respond(payload: dict):
    """Stand in for the HTTP layer, capturing what was sent."""
    captured = {}

    def fake(method, path, body=None):
        captured["method"], captured["path"], captured["body"] = method, path, body
        return payload

    return fake, captured


def test_pending_is_not_approved_and_carries_no_token():
    client = _client()
    fake, _ = _respond({"status": "pending"})
    with mock.patch.object(client, "_request", fake):
        result = client.register()
    assert result.pending
    assert not result.approved
    assert result.token == ""


def test_approved_with_a_token():
    client = _client()
    fake, _ = _respond({"status": "approved", "token": "raw-token-value"})
    with mock.patch.object(client, "_request", fake):
        result = client.register()
    assert result.approved
    assert result.token == "raw-token-value"


def test_approved_with_a_null_token_is_approved_but_tokenless():
    """The token was delivered on an earlier call and will not be repeated."""
    client = _client()
    fake, _ = _respond({"status": "approved", "token": None})
    with mock.patch.object(client, "_request", fake):
        result = client.register()
    assert result.approved
    assert result.token == ""


def test_register_sends_the_documented_payload():
    client = _client()
    fake, captured = _respond({"status": "pending"})
    with mock.patch.object(client, "_request", fake):
        client.register(label="Kitchen Pi")
    assert captured["method"] == "POST"
    assert captured["path"] == REGISTER_PATH
    assert captured["body"]["deviceId"] == "b0f1c2d3-4444-5555-6666-777788889999"
    assert captured["body"]["deviceType"] == "raspberry_pi"
    assert captured["body"]["label"] == "Kitchen Pi"


def test_label_is_capped_at_the_documented_length():
    client = _client()
    fake, captured = _respond({"status": "pending"})
    with mock.patch.object(client, "_request", fake):
        client.register(label="x" * 200)
    assert len(captured["body"]["label"]) == 120


def test_a_non_object_response_is_an_error():
    client = _client()
    fake, _ = _respond([1, 2, 3])
    with mock.patch.object(client, "_request", fake), pytest.raises(ApiError):
        client.register()


@pytest.mark.parametrize("weak", ["", "localhost", "ubuntu", "raspberrypi", "short"])
def test_useless_device_ids_are_replaced(weak, monkeypatch):
    """Ubuntu Core leaves every unit as 'localhost' — a fleet would enrol as one."""
    saved = {}
    monkeypatch.setattr(
        "visiontak_client.config.persist", lambda k, v, **kw: saved.update({k: v})
    )
    result = ensure_device_id(Config(device_id=weak))
    assert len(result.device_id) >= 8
    assert result.device_id.lower() not in {"localhost", "ubuntu", "raspberrypi"}
    assert saved["device-id"] == result.device_id


def test_an_existing_device_id_is_kept(monkeypatch):
    monkeypatch.setattr("visiontak_client.config.persist", lambda *a, **kw: None)
    existing = "b0f1c2d3-4444-5555-6666-777788889999"
    assert ensure_device_id(Config(device_id=existing)).device_id == existing


def test_device_id_survives_a_failure_to_persist(monkeypatch):
    """A device that cannot write its id must still start, with one for this boot."""

    def boom(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr("visiontak_client.config.persist", boom)
    assert len(ensure_device_id(Config(device_id="")).device_id) >= 8


def test_register_json_shape_is_serialisable():
    client = _client()
    fake, captured = _respond({"status": "pending"})
    with mock.patch.object(client, "_request", fake):
        client.register(label="Kitchen Pi")
    json.dumps(captured["body"])
