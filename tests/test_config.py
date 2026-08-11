import json

import pytest

from visiontak_client import config as config_module
from visiontak_client.config import Config


def test_defaults_are_usable_without_any_input(tmp_path):
    cfg = config_module.load(tmp_path, environ={})
    assert cfg.cec_backend == "auto"
    assert cfg.cec_device == "/dev/cec0"
    assert cfg.device_id  # falls back to the hostname


def test_snap_config_is_read_with_dashed_keys(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"server-url": "https://vt.example", "refresh-interval": "60"})
    )
    cfg = config_module.load(tmp_path, environ={})
    assert cfg.server_url == "https://vt.example"
    assert cfg.refresh_interval == 60


def test_environment_overrides_snap_config(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"server-url": "https://from-snap"}))
    cfg = config_module.load(tmp_path, environ={"VISIONTAK_SERVER_URL": "https://from-env"})
    assert cfg.server_url == "https://from-env"


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("no", False), ("1", True)])
def test_booleans_are_coerced(tmp_path, raw, expected):
    cfg = config_module.load(tmp_path, environ={"VISIONTAK_VERIFY_TLS": raw})
    assert cfg.verify_tls is expected


def test_bad_boolean_is_reported_against_its_field(tmp_path):
    with pytest.raises(ValueError, match="verify_tls"):
        config_module.load(tmp_path, environ={"VISIONTAK_VERIFY_TLS": "maybe"})


def test_unknown_cec_backend_is_rejected():
    with pytest.raises(ValueError, match="unknown cec_backend"):
        Config(cec_backend="magic")


def test_absurd_refresh_interval_is_rejected():
    with pytest.raises(ValueError, match="refresh_interval"):
        Config(refresh_interval=1)


def test_zero_intervals_mean_disabled_not_invalid():
    assert Config(refresh_interval=0, rotate_interval=0).rotate_interval == 0


def test_osd_name_is_capped_at_the_cec_limit():
    with pytest.raises(ValueError, match="osd_name"):
        Config(osd_name="VisionTAK Operations Centre")


def test_malformed_config_file_is_a_clear_error(tmp_path):
    (tmp_path / "config.json").write_text("{not json")
    with pytest.raises(ValueError, match="not valid client configuration"):
        config_module.load(tmp_path, environ={})


def test_write_snap_config_round_trips(tmp_path):
    config_module.write_snap_config(tmp_path, {"server-url": "https://vt.example"})
    assert config_module.load(tmp_path, environ={}).server_url == "https://vt.example"


def test_small_board_gets_the_low_memory_profile(tmp_path, monkeypatch):
    """A field unit has no login, so nobody can `snap set` it back from swapping."""
    monkeypatch.setattr(config_module, "_total_memory_mib", lambda: 950)
    cfg = config_module.load(tmp_path, environ={})
    assert cfg.max_live_views == 1
    assert cfg.hardware_acceleration == "never"


def test_explicit_settings_beat_the_low_memory_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "_total_memory_mib", lambda: 950)
    (tmp_path / config_module.CONFIG_BASENAME).write_text(
        '{"max-live-views": "3", "hardware-acceleration": "always"}'
    )
    cfg = config_module.load(tmp_path, environ={})
    assert cfg.max_live_views == 3
    assert cfg.hardware_acceleration == "always"


def test_larger_boards_keep_the_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "_total_memory_mib", lambda: 3800)
    cfg = config_module.load(tmp_path, environ={})
    assert cfg.max_live_views == 3
    assert cfg.hardware_acceleration == "auto"
