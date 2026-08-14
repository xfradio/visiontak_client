import pytest

from visiontak_client.discovery import discover_server_url, parse_option


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("10.0.0.5:3000", "http://10.0.0.5:3000"),
        ("10.0.0.5", "http://10.0.0.5"),
        ("http://10.0.0.5:3000", "http://10.0.0.5:3000"),
        ("https://visiontak.example", "https://visiontak.example"),
        ('"10.0.0.5:3000"', "http://10.0.0.5:3000"),
        ("  10.0.0.5:3000  ", "http://10.0.0.5:3000"),
    ],
)
def test_option_forms_people_actually_configure(payload, expected):
    assert parse_option(payload) == expected


def test_hex_encoded_option_is_decoded():
    """systemd renders an option it has no type for as hex."""
    # "10.0.0.5:3000"
    assert parse_option("31302e302e302e353a33303030") == "http://10.0.0.5:3000"


def test_colon_separated_hex_is_decoded():
    assert parse_option("31:30:2e:30:2e:30:2e:35") == "http://10.0.0.5"


def test_empty_and_junk_options_are_rejected():
    assert parse_option("") == ""
    assert parse_option("   ") == ""
    assert parse_option("ftp://nope") == ""


def _write_lease(tmp_path, body: str):
    lease = tmp_path / "2"
    lease.write_text(body)
    return (str(tmp_path),)


def test_discovers_from_a_lease(tmp_path):
    dirs = _write_lease(tmp_path, "ADDRESS=10.0.0.20\nOPTION_225=10.0.0.5:3000\n")
    assert discover_server_url(dirs) == "http://10.0.0.5:3000"


def test_private_option_spelling_is_accepted(tmp_path):
    dirs = _write_lease(tmp_path, "PRIVATE_225=visiontak.example\n")
    assert discover_server_url(dirs) == "http://visiontak.example"


def test_no_option_is_not_an_error(tmp_path):
    dirs = _write_lease(tmp_path, "ADDRESS=10.0.0.20\nROUTER=10.0.0.1\n")
    assert discover_server_url(dirs) == ""


def test_missing_lease_directory_is_not_an_error(tmp_path):
    assert discover_server_url((str(tmp_path / "absent"),)) == ""


def test_unusable_option_falls_through_rather_than_raising(tmp_path):
    """A malformed option must reach the setup screen, not stop the display."""
    dirs = _write_lease(tmp_path, "OPTION_225=;;;\n")
    assert discover_server_url(dirs) == ""


def test_describe_reports_no_lease(tmp_path):
    from visiontak_client.discovery import describe

    assert "no lease" in describe((str(tmp_path / "absent"),)).detail


def test_describe_reports_option_absent(tmp_path):
    from visiontak_client.discovery import describe

    dirs = _write_lease(tmp_path, "ADDRESS=10.0.0.20\n")
    result = describe(dirs)
    assert result.url == ""
    assert "not offered" in result.detail


def test_describe_reports_a_malformed_option(tmp_path):
    """Distinguishing 'unusable' from 'absent' is the whole point on a loginless unit."""
    from visiontak_client.discovery import describe

    dirs = _write_lease(tmp_path, "OPTION_225=;;;\n")
    result = describe(dirs)
    assert result.url == ""
    assert "unusable" in result.detail


def test_describe_reports_the_url_it_found(tmp_path):
    from visiontak_client.discovery import describe

    dirs = _write_lease(tmp_path, "OPTION_225=10.0.0.5:3000\n")
    result = describe(dirs)
    assert result.url == "http://10.0.0.5:3000"
    assert "10.0.0.5:3000" in result.detail


def test_discovery_is_off_by_default():
    """Nothing has made networkd request option 225 on a Core image, so it stays off."""
    from visiontak_client.config import Config

    assert Config().dhcp_discovery is False
