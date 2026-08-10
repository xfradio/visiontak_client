"""Runtime configuration.

Precedence, lowest to highest: built-in defaults, `$SNAP_DATA/config.json` (written by
the snap `configure` hook from `snap set`), then `VISIONTAK_*` environment variables
so a developer can run the kiosk on a desktop without snapd.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

CONFIG_BASENAME = "config.json"


@dataclass(frozen=True)
class Config:
    server_url: str = ""
    api_token: str = ""
    device_id: str = ""
    cec_backend: str = "auto"  # auto | kernel | libcec | none
    cec_device: str = "/dev/cec0"
    osd_name: str = "VisionTAK"
    refresh_interval: int = 300
    rotate_interval: int = 0
    start_dashboard: str = ""
    verify_tls: bool = True
    request_timeout: int = 15
    # Extra hosts the webviews may navigate to, beyond server-url and its subdomains.
    # Comma-separated; the single value "*" allows any host. Needed by dashboards that
    # embed third-party content, which would otherwise render blank.
    allowed_hosts: str = ""
    # How many dashboards keep a live WebView. Each one is a separate WebKit web
    # process holding its own render tree, so this is the dominant memory cost on a
    # small board: a Pi 3 B+ has 1 GiB shared with the GPU and cannot hold more than
    # one comfortably. Evicted dashboards reload on return.
    max_live_views: int = 3
    # auto | always | never. VideoCore IV (Pi 3) is a GL ES 2.0 part and forcing
    # accelerated compositing on it is usually slower and less stable than the
    # software path; "auto" leaves the decision to WebKit.
    hardware_acceleration: str = "auto"

    def __post_init__(self) -> None:
        if self.refresh_interval and self.refresh_interval < 10:
            raise ValueError("refresh_interval must be 0 or >= 10 seconds")
        if self.rotate_interval and self.rotate_interval < 5:
            raise ValueError("rotate_interval must be 0 or >= 5 seconds")
        if self.cec_backend not in {"auto", "kernel", "libcec", "none"}:
            raise ValueError(f"unknown cec_backend: {self.cec_backend}")
        if self.max_live_views < 1:
            raise ValueError("max_live_views must be at least 1")
        if self.hardware_acceleration not in {"auto", "always", "never"}:
            raise ValueError(f"unknown hardware_acceleration: {self.hardware_acceleration}")
        # CEC OSD names are capped at 14 bytes by the spec; the kernel truncates
        # silently, which produces confusing names in the TV's source list.
        if len(self.osd_name.encode()) > 14:
            raise ValueError("osd_name must be at most 14 bytes")

    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("SNAP_DATA") or os.environ.get("VISIONTAK_DATA_DIR") or ".")

    @property
    def cache_dir(self) -> Path:
        return Path(os.environ.get("SNAP_COMMON") or self.data_dir) / "cache"


_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}


def _coerce(raw: Any, target: type) -> Any:
    if target is bool:
        text = str(raw).strip().lower()
        if text in _BOOL_TRUE:
            return True
        if text in _BOOL_FALSE:
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    if target is int:
        return int(str(raw).strip())
    return str(raw).strip()


def _from_mapping(source: dict[str, Any], *, key_style: str) -> dict[str, Any]:
    """Pull known config fields out of a mapping, ignoring anything unrecognised.

    Values are left raw here; `load` does the single, typed coercion pass.
    """
    out: dict[str, Any] = {}
    for field in fields(Config):
        if key_style == "env":
            key = "VISIONTAK_" + field.name.upper()
        else:
            key = field.name.replace("_", "-")
        if key not in source or source[key] in (None, ""):
            continue
        out[field.name] = source[key]
    return out


def load(data_dir: Path | None = None, environ: dict[str, str] | None = None) -> Config:
    environ = dict(os.environ if environ is None else environ)
    base = Path(environ.get("SNAP_DATA") or environ.get("VISIONTAK_DATA_DIR") or ".")
    path = (data_dir or base) / CONFIG_BASENAME

    values: dict[str, Any] = {}
    if path.is_file():
        try:
            values.update(_from_mapping(json.loads(path.read_text()), key_style="snap"))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{path} is not valid client configuration: {exc}") from exc
    values.update(_from_mapping(environ, key_style="env"))

    values.setdefault("device_id", socket.gethostname())
    # `from __future__ import annotations` turns field.type into a string, so derive the
    # target type from each field's default instead.
    for field in fields(Config):
        if field.name in values:
            try:
                values[field.name] = _coerce(values[field.name], type(field.default))
            except ValueError as exc:
                raise ValueError(f"invalid value for {field.name}: {exc}") from exc
    return Config(**values)


def write_snap_config(data_dir: Path, values: dict[str, Any]) -> Path:
    """Used by the snap `configure` hook."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / CONFIG_BASENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(values, indent=2, sort_keys=True))
    tmp.replace(path)
    return path
