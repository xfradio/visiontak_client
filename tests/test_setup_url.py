import json

import pytest

from visiontak_client.config import (
    CONFIG_BASENAME,
    STATE_BASENAME,
    load,
    normalise_server_url,
    persist,
)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("https://visiontak.example", "https://visiontak.example"),
        ("http://10.0.0.5:3000", "http://10.0.0.5:3000"),
        # What someone actually types on a remote, with no scheme.
        ("10.0.0.5:3000", "http://10.0.0.5:3000"),
        ("visiontak.local", "http://visiontak.local"),
        # Trailing slashes and stray whitespace are the norm, not an error.
        ("  https://vt.example/  ", "https://vt.example"),
    ],
)
def test_typed_addresses_are_made_usable(typed, expected):
    assert normalise_server_url(typed) == expected


@pytest.mark.parametrize("typed", ["", "   ", "ftp://vt.example", "://nope", "http://"])
def test_unusable_addresses_are_rejected(typed):
    assert normalise_server_url(typed) == ""


def test_persist_writes_self_config_not_the_hooks_file(tmp_path):
    """config.json belongs to the configure hook and is regenerated from snap set."""
    persist("server-url", "http://vt.example", data_dir=tmp_path, environ={})
    written = json.loads((tmp_path / STATE_BASENAME).read_text())
    assert written["server-url"] == "http://vt.example"
    assert not (tmp_path / CONFIG_BASENAME).exists()


def test_persist_keeps_existing_keys(tmp_path):
    (tmp_path / STATE_BASENAME).write_text(json.dumps({"device-id": "visiontak_client_x"}))
    persist("server-url", "http://vt.example", data_dir=tmp_path, environ={})
    written = json.loads((tmp_path / STATE_BASENAME).read_text())
    assert written == {"device-id": "visiontak_client_x", "server-url": "http://vt.example"}


def test_client_state_survives_the_hook_rewriting_config_json(tmp_path):
    """The regression: a registered device came back to setup with its address gone.

    persist() wrote server-url, then approving the device triggered the configure
    hook, which regenerated config.json from snapd's config — where server-url had
    never been set — and the address vanished.
    """
    persist("server-url", "http://vt.example", data_dir=tmp_path, environ={})
    # The hook regenerates config.json holding only what `snap set` knows about.
    (tmp_path / CONFIG_BASENAME).write_text(json.dumps({"cec-backend": "none"}))

    cfg = load(tmp_path, environ={})
    assert cfg.server_url == "http://vt.example"
    assert cfg.cec_backend == "none"


def test_snap_set_overrides_client_state(tmp_path):
    """An admin setting an address explicitly must beat a discovered one."""
    persist("server-url", "http://discovered.example", data_dir=tmp_path, environ={})
    (tmp_path / CONFIG_BASENAME).write_text(json.dumps({"server-url": "http://admin.example"}))
    assert load(tmp_path, environ={}).server_url == "http://admin.example"


@pytest.mark.parametrize(
    "junk",
    [";;;", "http://;;;", "http:// ", "10.0.0.5:notaport", "10.0.0.5:0", "10.0.0.5:99999"],
)
def test_junk_authorities_are_rejected(junk):
    """A bad DHCP option must fall through to setup, not be saved as the server."""
    assert normalise_server_url(junk) == ""


def test_ipv6_literals_are_accepted():
    assert normalise_server_url("[2001:db8::1]:3000") == "http://[2001:db8::1]:3000"
