import socket

from visiontak_client import netinfo


def test_returns_an_address_for_a_routable_target():
    """connect() on a UDP socket selects an interface; nothing is transmitted."""
    result = netinfo.local_ip("http://10.0.0.5:3000")
    # Any answer is machine-dependent, but it must be a dotted quad or empty.
    assert result == "" or result.count(".") == 3


def test_a_malformed_server_url_does_not_raise():
    assert netinfo.local_ip("not a url") in ("", *[netinfo.local_ip("")])


def test_no_route_is_empty_not_an_error(monkeypatch):
    """A display with no network must still render its diagnostics overlay."""

    class Dead:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def settimeout(self, _):
            pass

        def connect(self, _):
            raise OSError("network is unreachable")

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: Dead())
    assert netinfo.local_ip("http://vt.example") == ""


def test_the_server_host_is_used_as_the_route_target(monkeypatch):
    """Which interface answers matters: the one that reaches the server is the one
    support will connect back on."""
    seen = {}

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def settimeout(self, _):
            pass

        def connect(self, addr):
            seen["addr"] = addr

        def getsockname(self):
            return ("192.0.2.10", 9)

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: Probe())
    assert netinfo.local_ip("https://vt.example:3001/x") == "192.0.2.10"
    assert seen["addr"][0] == "vt.example"
