"""Check a VisionTAK Server from a device's point of view.

Answers "will the kiosk work against this server, with this token?" without needing a
display, a Pi, or a snap install:

    python -m visiontak_client.probe http://localhost:3001 --token "$TOKEN"

Read-only — it only issues GETs.

Note the server is a Next.js app with a catch-all route: an unknown path returns the
SPA shell with HTTP 200, so "200" alone proves nothing. Every check below verifies the
content type is JSON before believing the response.
"""

from __future__ import annotations

import argparse
import sys

from .api import ApiError, AuthError, VisionTakClient
from .config import Config
from .models import view_url

# Endpoints the client itself relies on, plus the admin ones, which are probed only to
# report on their exposure.
REQUIRED_PATH = "/api/v1/client/config"
ADMIN_PATHS = ("/api/v1/dashboards", "/api/v1/layouts")


def check_client_config(client: VisionTakClient) -> int:
    print(f"\n{REQUIRED_PATH}")
    try:
        client_config = client.fetch_client_config()
    except AuthError as exc:
        print(f"  ✗ {exc}")
        print("    The kiosk cannot start with this token.")
        return 1
    except ApiError as exc:
        print(f"  ✗ {exc}")
        return 1

    print(f"  ✓ {len(client_config.dashboards)} dashboard(s) allowed")
    print(f"    defaultDashboardId: {client_config.default_dashboard_id or '(none)'}")
    for index, dashboard in enumerate(client_config.dashboards, start=1):
        print(f"    {index}. {dashboard.name}")
        print(f"       {dashboard.url}")
    if not client_config.dashboards:
        print("    ! The kiosk will show 'No dashboards available'.")
        return 1
    return 0


def check_render_pages(client: VisionTakClient, dashboard_ids: list[str]) -> int:
    """The webview loads these, so confirm they are reachable and not the sign-in shell."""
    print("\nrender pages")
    failures = 0
    for dashboard_id in dashboard_ids:
        url = view_url(client.base_url, dashboard_id)
        title, error = _fetch_title(client, url)
        if error:
            print(f"  ✗ {url}\n      {error}")
            failures += 1
        elif "sign in" in title.lower():
            print(f"  ✗ {url}\n      redirected to sign-in: the kiosk cannot log in interactively")
            failures += 1
        else:
            print(f"  ✓ {url}\n      title: {title}")
    return failures


def _fetch_title(client: VisionTakClient, url: str) -> tuple[str, str]:
    import re
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, method="GET")
    request.add_header("User-Agent", "VisionTAK-Kiosk (probe)")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(200_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return "", str(exc)
    match = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    return (match.group(1).strip() if match else "(no title)"), ""


def report_admin_exposure(client: VisionTakClient) -> None:
    print("\nadmin endpoints (informational)")
    for path in ADMIN_PATHS:
        unauthenticated = VisionTakClient(
            Config(server_url=client.base_url, api_token="", device_id="probe")
        )
        try:
            unauthenticated._request("GET", path)  # noqa: SLF001 - diagnostic tool
        except AuthError:
            print(f"  ✓ {path} requires a token")
        except ApiError as exc:
            print(f"  · {path} {exc}")
        else:
            print(f"  ! {path} answers with NO token — readable by anything on the network")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="visiontak-probe", description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--token", default="")
    parser.add_argument("--insecure", action="store_true", help="skip TLS verification")
    parser.add_argument("--skip-pages", action="store_true", help="do not fetch render pages")
    args = parser.parse_args(argv)

    client = VisionTakClient(
        Config(
            server_url=args.base_url,
            api_token=args.token,
            device_id="probe",
            verify_tls=not args.insecure,
        )
    )
    print(f"Probing {client.base_url}")

    failures = check_client_config(client)
    if not failures and not args.skip_pages:
        ids = [d.id for d in client.fetch_client_config().dashboards]
        failures += check_render_pages(client, ids)
    report_admin_exposure(client)

    print("\nOK" if not failures else f"\n{failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
