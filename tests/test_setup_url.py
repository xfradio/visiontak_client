import json

import pytest

from visiontak_client.config import CONFIG_BASENAME, normalise_server_url, persist


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


def test_persist_writes_the_file_outside_a_snap(tmp_path):
    persist("server-url", "http://vt.example", data_dir=tmp_path, environ={})
    written = json.loads((tmp_path / CONFIG_BASENAME).read_text())
    assert written["server-url"] == "http://vt.example"


def test_persist_keeps_existing_keys(tmp_path):
    (tmp_path / CONFIG_BASENAME).write_text(json.dumps({"cec-backend": "none"}))
    persist("server-url", "http://vt.example", data_dir=tmp_path, environ={})
    written = json.loads((tmp_path / CONFIG_BASENAME).read_text())
    assert written == {"cec-backend": "none", "server-url": "http://vt.example"}
