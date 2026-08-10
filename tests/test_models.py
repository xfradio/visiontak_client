import pytest

from visiontak_client.models import Dashboard, sort_dashboards, view_url

BASE = "http://localhost:3001"
# Exactly as returned by GET /api/v1/client/config on the live server.
LIVE_ENTRY = {"id": "26f96ec8-e09c-4e1f-9b57-1e9da2d87a98", "name": "Shack TV"}
# Exactly as returned by GET /api/v1/dashboards on the live server.
LIVE_ADMIN_ENTRY = {
    "id": "26f96ec8-e09c-4e1f-9b57-1e9da2d87a98",
    "name": "Shack TV",
    "description": None,
    "isEnabled": True,
    "layoutId": "c38f844a-f23b-45db-b2ac-573161b79edf",
    "sortOrder": 0,
}


def test_live_client_config_entry():
    dashboard = Dashboard.from_payload(LIVE_ENTRY, BASE)
    assert dashboard.id == "26f96ec8-e09c-4e1f-9b57-1e9da2d87a98"
    assert dashboard.name == "Shack TV"
    assert dashboard.url == f"{BASE}/view/26f96ec8-e09c-4e1f-9b57-1e9da2d87a98"


def test_live_admin_entry_uses_sort_order():
    assert Dashboard.from_payload(LIVE_ADMIN_ENTRY, BASE).order == 0
    entry = dict(LIVE_ADMIN_ENTRY, sortOrder=7)
    assert Dashboard.from_payload(entry, BASE).order == 7


def test_view_url_does_not_double_up_slashes():
    assert view_url("http://localhost:3001/", "abc") == "http://localhost:3001/view/abc"
    assert view_url("http://localhost:3001", "abc") == "http://localhost:3001/view/abc"


def test_entry_without_an_id_is_rejected():
    with pytest.raises(ValueError, match="no id"):
        Dashboard.from_payload({"name": "Shack TV"}, BASE)


def test_missing_name_falls_back_to_the_id():
    assert Dashboard.from_payload({"id": "abc"}, BASE).name == "abc"


def test_group_defaults_to_empty_and_is_read_when_present():
    # The chooser reads dashboard.group to build the row subtitle. The live
    # client-config payload has no such key, so it has to default rather than raise —
    # this crashed every menu build against a real server.
    assert Dashboard.from_payload(LIVE_ENTRY, BASE).group == ""
    entry = dict(LIVE_ENTRY, groupName="Workshop")
    assert Dashboard.from_payload(entry, BASE).group == "Workshop"


@pytest.mark.parametrize("alias", ["id", "dashboardId", "dashboard_id", "uuid"])
def test_id_aliases(alias):
    assert Dashboard.from_payload({alias: "abc"}, BASE).id == "abc"


def test_enabled_defaults_to_true_when_the_flag_is_absent():
    """The client-config endpoint omits isEnabled; absent must not mean hidden."""
    assert Dashboard.is_enabled(LIVE_ENTRY) is True


def test_disabled_dashboards_are_detected():
    assert Dashboard.is_enabled(dict(LIVE_ADMIN_ENTRY, isEnabled=False)) is False


def test_non_numeric_sort_order_does_not_explode():
    assert Dashboard.from_payload({"id": "a", "sortOrder": "abc"}, BASE).order == 0


def test_sorting_is_by_order_then_name():
    dashboards = [
        Dashboard("c", "Charlie", "u", order=1),
        Dashboard("a", "alpha", "u", order=0),
        Dashboard("b", "Bravo", "u", order=0),
    ]
    assert [d.id for d in sort_dashboards(dashboards)] == ["a", "b", "c"]
