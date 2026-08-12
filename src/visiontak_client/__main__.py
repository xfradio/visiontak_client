"""Entry point: `visiontak-client` / `python -m visiontak_client`."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

from . import __version__
from . import config as config_module

log = logging.getLogger("visiontak")

WAYLAND_WAIT_SECONDS = 60


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="visiontak-client", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    parser.add_argument(
        "--check-config", action="store_true", help="validate configuration and exit"
    )
    parser.add_argument(
        "--wait-for-wayland",
        type=int,
        default=WAYLAND_WAIT_SECONDS,
        help="seconds to wait for the compositor socket before giving up",
    )
    return parser


def wait_for_wayland(timeout: int) -> bool:
    """Ubuntu Frame and this snap start in parallel; the socket may not exist yet."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    display = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    if not runtime_dir:
        return False
    socket_path = os.path.join(runtime_dir, display)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(socket_path):
            return True
        log.info("waiting for compositor socket %s", socket_path)
        time.sleep(1.0)
    return os.path.exists(socket_path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        cfg = config_module.load()
    except ValueError as exc:
        log.error("configuration error: %s", exc)
        return 2

    # First boot: ask the network before asking a person. Skipped under
    # --check-config, which must stay a read-only validation the configure hook can
    # run during `snap set` without side effects.
    if not args.check_config:
        cfg = config_module.ensure_device_id(cfg)
        if not cfg.server_url and cfg.dhcp_discovery:
            from .firstrun import discover

            cfg = discover(cfg)

    if not cfg.server_url:
        # Incomplete is not invalid. snapd runs the configure hook during `snap install`,
        # before anything can be `snap set`, so failing here would make the snap
        # impossible to install on a bare device — the hook fails, and snapd rolls the
        # whole install back. Genuinely bad values still raise ValueError above and
        # exit 2, so `snap set` is still rejected for anything unusable.
        log.warning("server-url is not set. Run: snap set visiontak-client server-url=https://…")
        # Keep running: the display should come up and say so rather than crash-loop.
    if args.check_config:
        log.info("configuration OK: server=%s device=%s cec=%s",
                 cfg.server_url, cfg.device_id, cfg.cec_backend)
        return 0

    # Imported here so --check-config works on a machine without GTK.
    from .controller import KioskController
    from .ui.app import KioskApplication, enforce_wayland

    enforce_wayland()
    if not wait_for_wayland(args.wait_for_wayland):
        log.error(
            "no Wayland compositor after %ds — is ubuntu-frame running?", args.wait_for_wayland
        )
        return 1

    application = KioskApplication(cfg)
    controller = KioskController(cfg)
    application.set_controller(controller)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (controller.stop(), application.quit()))

    try:
        return application.run([])
    finally:
        controller.stop()


if __name__ == "__main__":
    sys.exit(main())
