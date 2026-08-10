"""Where the kiosk finds its brand assets.

Deliberately GTK-free so it stays testable without a display, and deliberately
tolerant: a missing logo degrades to a text splash rather than stopping a display
from coming up in the field.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

LOGO_BASENAME = "visiontak-logo.png"


def logo_path(environ: dict[str, str] | None = None) -> Path | None:
    """The logo to show on the splash, or None if the build shipped without one."""
    environ = dict(os.environ if environ is None else environ)
    candidates: list[Path] = []

    snap = environ.get("SNAP")
    if snap:
        candidates.append(Path(snap) / "assets" / LOGO_BASENAME)
    # A source checkout, for `make run-local` and the docker harness.
    candidates.append(Path(__file__).resolve().parents[2] / "assets" / LOGO_BASENAME)

    for path in candidates:
        if path.is_file():
            return path
    log.info("no %s found; falling back to a text splash", LOGO_BASENAME)
    return None
