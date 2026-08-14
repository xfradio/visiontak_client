from visiontak_client.ui.policy import HostAllowlist


def test_configured_host_is_permitted():
    allowlist = HostAllowlist(["https://vt.example/api"])
    assert allowlist.permits("https://vt.example/d/ops")


def test_subdomains_of_a_permitted_host_are_allowed():
    allowlist = HostAllowlist(["https://visiontak.example"])
    assert allowlist.permits("https://grafana.visiontak.example/d/1")


def test_lookalike_suffix_is_not_allowed():
    """evilvisiontak.example must not slip past an endswith check."""
    allowlist = HostAllowlist(["https://visiontak.example"])
    assert not allowlist.permits("https://evilvisiontak.example/")


def test_unknown_host_is_blocked():
    assert not HostAllowlist(["https://vt.example"]).permits("https://elsewhere.test/")


def test_hosts_are_matched_case_insensitively():
    assert HostAllowlist(["https://VT.Example"]).permits("https://vt.EXAMPLE/d")


def test_wildcard_allows_any_host():
    allowlist = HostAllowlist(["https://vt.example", "*"])
    assert allowlist.allows_any
    assert allowlist.permits("https://pskreporter.info/pskmap.html")
    assert allowlist.permits("http://anything.test/x")


def test_wildcard_is_off_unless_asked_for():
    allowlist = HostAllowlist(["https://vt.example"])
    assert not allowlist.allows_any
    assert not allowlist.permits("https://pskreporter.info/pskmap.html")


def test_bare_hostnames_are_accepted():
    """`allowed-hosts` is a host list, not a URL list — urlparse needs the // hint."""
    allowlist = HostAllowlist(["pskreporter.info"])
    assert allowlist.permits("https://pskreporter.info/pskmap.html")
    assert allowlist.permits("https://tiles.pskreporter.info/x.png")
    assert not allowlist.permits("https://evilpskreporter.info/")


def test_about_and_data_urls_are_always_allowed():
    allowlist = HostAllowlist(["https://vt.example"])
    assert allowlist.permits("about:blank")
    assert allowlist.permits("data:text/html,<h1>hi</h1>")


def test_file_urls_are_not_allowed():
    assert not HostAllowlist(["https://vt.example"]).permits("file:///etc/passwd")


def test_dashboards_extend_the_allowlist():
    allowlist = HostAllowlist(["https://vt.example"])
    assert not allowlist.permits("https://tiles.test/x")
    allowlist.allow("https://tiles.test/style.json")
    assert allowlist.permits("https://tiles.test/x")


def test_empty_allowlist_permits_nothing_remote():
    assert not HostAllowlist([]).permits("https://vt.example")


def test_navigation_is_open_by_default():
    """Dashboards embed third-party content; a closed default blanks tiles silently."""
    from visiontak_client.config import Config
    from visiontak_client.ui.policy import HostAllowlist

    seed = [h.strip() for h in Config().allowed_hosts.split(",") if h.strip()]
    assert HostAllowlist(seed).allows_any


def test_an_explicit_list_still_restricts():
    from visiontak_client.config import Config
    from visiontak_client.ui.policy import HostAllowlist

    cfg = Config(allowed_hosts="grafana.example")
    seed = [h.strip() for h in cfg.allowed_hosts.split(",") if h.strip()]
    allowlist = HostAllowlist(seed)
    assert not allowlist.allows_any
    assert allowlist.permits("https://grafana.example/d/1")
    assert not allowlist.permits("https://youtube.com/embed/x")
