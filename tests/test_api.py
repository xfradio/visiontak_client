import json

import pytest

from visiontak_client.api import (
    ApiError,
    ClientConfig,
    ConfigCache,
    parse_client_config,
    unwrap_collection,
)
from visiontak_client.models import Dashboard

BASE = "http://localhost:3001"

# Captured verbatim from GET /api/v1/client/config on the live server.
LIVE_RESPONSE = {
    "defaultDashboardId": None,
    "allowedDashboards": [{"id": "26f96ec8-e09c-4e1f-9b57-1e9da2d87a98", "name": "Shack TV"}],
}


def test_live_response_parses():
    client_config = parse_client_config(LIVE_RESPONSE, BASE)
    assert client_config.default_dashboard_id is None
    assert len(client_config.dashboards) == 1
    dashboard = client_config.dashboards[0]
    assert dashboard.name == "Shack TV"
    assert dashboard.url == f"{BASE}/view/26f96ec8-e09c-4e1f-9b57-1e9da2d87a98"


def test_default_dashboard_id_is_carried_through():
    payload = dict(LIVE_RESPONSE, defaultDashboardId="26f96ec8-e09c-4e1f-9b57-1e9da2d87a98")
    assert parse_client_config(payload, BASE).default_dashboard_id.startswith("26f96ec8")


def test_null_default_is_none_not_the_string_none():
    assert parse_client_config(LIVE_RESPONSE, BASE).default_dashboard_id is None


def test_empty_allowed_list_is_valid_and_empty():
    payload = {"defaultDashboardId": None, "allowedDashboards": []}
    assert parse_client_config(payload, BASE).dashboards == []


@pytest.mark.parametrize("key", ["allowedDashboards", "dashboards", "data", "items", "results"])
def test_envelope_variants_are_unwrapped(key):
    assert unwrap_collection({key: [{"id": "a"}]}) == [{"id": "a"}]


def test_unrecognisable_payload_raises():
    with pytest.raises(ApiError, match="no dashboard collection"):
        unwrap_collection({"unexpected": {"nested": "object"}})


def test_disabled_dashboards_are_filtered_out():
    payload = {
        "dashboards": [
            {"id": "a", "name": "A", "isEnabled": True},
            {"id": "b", "name": "B", "isEnabled": False},
        ]
    }
    assert [d.id for d in parse_client_config(payload, BASE).dashboards] == ["a"]


def test_one_bad_entry_does_not_lose_the_whole_list():
    """A wall display must not go blank because a single dashboard is malformed."""
    payload = {"allowedDashboards": [{"id": "a", "name": "A"}, {"name": "no id"}, "junk"]}
    assert [d.id for d in parse_client_config(payload, BASE).dashboards] == ["a"]


def test_results_are_in_display_order():
    payload = {
        "dashboards": [
            {"id": "b", "name": "B", "sortOrder": 5},
            {"id": "a", "name": "A", "sortOrder": 1},
        ]
    }
    assert [d.id for d in parse_client_config(payload, BASE).dashboards] == ["a", "b"]


def test_cache_round_trip(tmp_path):
    cache = ConfigCache(tmp_path / "cc.json")
    original = parse_client_config(LIVE_RESPONSE, BASE)
    cache.save(original)
    assert cache.load(BASE) == original


def test_cache_preserves_the_default_dashboard(tmp_path):
    cache = ConfigCache(tmp_path / "cc.json")
    cache.save(ClientConfig([Dashboard("a", "A", "u")], default_dashboard_id="a"))
    assert cache.load(BASE).default_dashboard_id == "a"


def test_missing_cache_is_empty_not_an_error(tmp_path):
    assert ConfigCache(tmp_path / "absent.json").load(BASE) == ClientConfig()


def test_corrupt_cache_is_discarded_rather_than_crashing_the_kiosk(tmp_path):
    path = tmp_path / "cc.json"
    path.write_text("{{{")
    assert ConfigCache(path).load(BASE) == ClientConfig()


def test_cache_write_is_atomic(tmp_path):
    """A power cut mid-write must not leave a half-file where the real one was."""
    path = tmp_path / "cc.json"
    cache = ConfigCache(path)
    cache.save(ClientConfig([Dashboard("a", "A", "u")]))
    cache.save(ClientConfig([Dashboard("b", "B", "u")]))
    assert [d["id"] for d in json.loads(path.read_text())["allowedDashboards"]] == ["b"]
    assert not list(tmp_path.glob("*.tmp"))


def test_an_unchanged_config_is_not_rewritten(tmp_path):
    """The refresh loop saves on every poll. Rewriting an identical file hundreds of
    times a day spends SD card write cycles for nothing."""
    path = tmp_path / "cc.json"
    cache = ConfigCache(path)
    config = ClientConfig(dashboards=[Dashboard(id="a", name="A", url=f"{BASE}/view/a")])

    cache.save(config)
    first = path.stat().st_mtime_ns

    cache.save(config)
    assert path.stat().st_mtime_ns == first, "identical config was written again"


def test_a_changed_config_is_written(tmp_path):
    path = tmp_path / "cc.json"
    cache = ConfigCache(path)
    cache.save(ClientConfig(dashboards=[Dashboard(id="a", name="A", url=f"{BASE}/view/a")]))
    cache.save(ClientConfig(dashboards=[Dashboard(id="b", name="B", url=f"{BASE}/view/b")]))
    assert "b" in json.loads(path.read_text())["allowedDashboards"][0]["id"]


def test_a_deleted_cache_file_is_recreated(tmp_path):
    """Skipping the write must not depend on memory alone — the file is the artefact."""
    path = tmp_path / "cc.json"
    cache = ConfigCache(path)
    config = ClientConfig(dashboards=[Dashboard(id="a", name="A", url=f"{BASE}/view/a")])
    cache.save(config)
    path.unlink()
    cache.save(config)
    assert path.is_file()
