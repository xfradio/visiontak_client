"""Runtime configuration.

Precedence, lowest to highest: built-in defaults, `$SNAP_DATA/config.json` (written by
the snap `configure` hook from `snap set`), then `VISIONTAK_*` environment variables
so a developer can run the kiosk on a desktop without snapd.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import socket
import subprocess
import uuid
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

CONFIG_BASENAME = "config.json"

# Written by the client itself — a discovered address, an issued token, a generated
# device id. Deliberately NOT config.json: the configure hook regenerates that file
# from snapd's configuration, so anything the client put there is dropped the next time
# the hook runs. That produced a device which registered successfully and then came
# back to the setup screen with its address gone.
STATE_BASENAME = "self-config.json"


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


log = logging.getLogger(__name__)

# A Pi 3 B+ reports about 950 MiB once the GPU split is taken out; a Pi 4 reports
# 1900+. Anything under this is treated as the small board.
LOW_MEMORY_MIB = 1536


def _total_memory_mib() -> int | None:
    """Total RAM in MiB, or None where /proc/meminfo is not readable."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


# Hostnames that identify nothing. Ubuntu Core leaves every unit as "localhost", so
# the old hostname fallback would have enrolled an entire fleet as one device.
_WEAK_DEVICE_IDS = {"localhost", "localhost.localdomain", "ubuntu", "raspberrypi", ""}

# The register endpoint requires 8-128 characters.
DEVICE_ID_MIN_LEN = 8

# Prefixed so a device is identifiable as one of ours in the server's device list and
# in logs, rather than a bare UUID among whatever else registers. 17 + 36 = 53
# characters, comfortably inside the limit.
DEVICE_ID_PREFIX = "visiontak_client_"


def ensure_device_id(config: Config) -> Config:
    """Give this device a stable, unique id, persisting it the first time.

    Registration identifies a device by this value on every call, so it has to be
    unique across the fleet and survive reboots. A UUID satisfies both; the hostname
    satisfies neither on Ubuntu Core.
    """
    current = config.device_id.strip()
    if current.lower() not in _WEAK_DEVICE_IDS and len(current) >= DEVICE_ID_MIN_LEN:
        # Kept as-is even without the prefix. Changing the id of a device that has
        # already registered would orphan its approval and enrol it again as a
        # stranger, which is worse than an inconsistent name.
        return config

    new_id = f"{DEVICE_ID_PREFIX}{uuid.uuid4()}"
    try:
        persist("device-id", new_id)
    except Exception as exc:  # noqa: BLE001 - a headless device must still start
        log.warning("could not persist device-id %s: %s", new_id, exc)
    else:
        log.info("assigned this device the id %s", new_id)
    return dataclasses.replace(config, device_id=new_id)


def normalise_server_url(raw: str) -> str:
    """Make a typed address usable, or return '' if it cannot be.

    People type "10.0.0.5:3000" on a remote, not "http://10.0.0.5:3000/". Accepting
    only the pedantic form would fail exactly the users the setup screen exists for.
    Lives here rather than beside the screen so it is testable without GTK.
    """
    text = raw.strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    # Split before trimming slashes: stripping them first turns "http://" into
    # "http:", which then looks scheme-less and becomes "http://http:".
    scheme, _, rest = text.partition("://")
    rest = rest.rstrip("/")
    if scheme.lower() not in {"http", "https"} or not rest:
        return ""
    if not _valid_authority(rest.split("/", 1)[0]):
        return ""
    return f"{scheme.lower()}://{rest}"


def _valid_authority(authority: str) -> bool:
    """Check host[:port] really is one.

    Without this, junk becomes a URL — ";;;" would pass as "http://;;;". That is worse
    than rejecting it: a bad DHCP option would be persisted as the server address, and
    a field unit with no login would sit pointing at nonsense forever instead of
    falling back to the setup screen.
    """
    if authority.startswith("["):  # IPv6 literal
        close = authority.find("]")
        if close < 2:
            return False
        host, port = authority[1:close], authority[close + 1 :]
        if port and not (port.startswith(":") and _valid_port(port[1:])):
            return False
        return bool(re.fullmatch(r"[0-9A-Fa-f:.]+", host))

    host, sep, port = authority.partition(":")
    if sep and not _valid_port(port):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?", host))


def _valid_port(port: str) -> bool:
    return port.isdigit() and 0 < int(port) <= 65535


def persist(
    key: str,
    value: str,
    *,
    data_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> None:
    """Record one setting the client worked out for itself.

    Kept in self-config.json rather than config.json, which the configure hook owns
    and regenerates from `snap set` values. It is also mirrored into snapd's config
    where possible, so `snap get` shows the truth and an admin can override it — but
    that is best effort, and the file is what the client relies on.
    """
    environ = dict(os.environ if environ is None else environ)

    # Always record it in our own file. Writing only through snapctl would lose the
    # value whenever snapctl is unavailable, and writing only to config.json would
    # lose it the next time the configure hook regenerates that file.
    base = Path(environ.get("SNAP_DATA") or environ.get("VISIONTAK_DATA_DIR") or ".")
    path = (data_dir or base) / STATE_BASENAME
    current: dict[str, Any] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text())
        except json.JSONDecodeError:
            current = {}
    current[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)

    # Mirror into snapd so `snap get` reflects reality. Failure is not fatal: the
    # value is already durable above, and snapctl is not always usable outside a hook.
    if environ.get("SNAP_INSTANCE_NAME") or environ.get("SNAP_NAME"):
        try:
            subprocess.run(["snapctl", "set", f"{key}={value}"], check=True, timeout=30)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("could not mirror %s into snap config: %s", key, exc)


def load(data_dir: Path | None = None, environ: dict[str, str] | None = None) -> Config:
    environ = dict(os.environ if environ is None else environ)
    base = Path(environ.get("SNAP_DATA") or environ.get("VISIONTAK_DATA_DIR") or ".")
    path = (data_dir or base) / CONFIG_BASENAME

    values: dict[str, Any] = {}

    # Client-written state first, so an explicit `snap set` still overrides it.
    state_path = (data_dir or base) / STATE_BASENAME
    if state_path.is_file():
        try:
            values.update(_from_mapping(json.loads(state_path.read_text()), key_style="snap"))
        except (json.JSONDecodeError, ValueError) as exc:
            # Corrupt self-config must not stop a display starting; it is recoverable
            # by rediscovery or the setup screen.
            log.warning("ignoring unreadable %s: %s", state_path, exc)

    if path.is_file():
        try:
            values.update(_from_mapping(json.loads(path.read_text()), key_style="snap"))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{path} is not valid client configuration: {exc}") from exc
    values.update(_from_mapping(environ, key_style="env"))

    values.setdefault("device_id", socket.gethostname())

    # A field unit boots with console-conf disabled and no login, so nobody can run
    # `snap set` on it. Defaults sized for a Pi 4 would swap or OOM a Pi 3 B+ with
    # nobody able to intervene, so read the board instead. Explicit settings win.
    total_mib = _total_memory_mib()
    if total_mib is not None and total_mib < LOW_MEMORY_MIB:
        if "max_live_views" not in values:
            values["max_live_views"] = 1
        if "hardware_acceleration" not in values:
            values["hardware_acceleration"] = "never"
        log.info(
            "%d MiB of RAM detected — using the low-memory profile "
            "(max_live_views=%s, hardware_acceleration=%s)",
            total_mib,
            values["max_live_views"],
            values["hardware_acceleration"],
        )
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
