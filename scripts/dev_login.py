"""Print a dashboard session cookie for a local backend running on GITHUB_DEV_TOKEN.

The browser OAuth flow needs a registered GitHub App. Local setups without one
sign in through ``/dashboard/api/auth/dev-login`` instead; in a browser just
open that URL. This is the same thing for a terminal, so curl can call the
dashboard API.

    uv run python scripts/dev_login.py            # the cookie value
    uv run python scripts/dev_login.py --curl     # a ready --cookie flag
"""

import argparse
import json
import sys

import httpx2

COOKIE_NAME = "osw_session"
DEFAULT_BASE_URL = "http://localhost:2024"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--curl", action="store_true", help="print a curl --cookie flag")
    parser.add_argument("--json", action="store_true", help="print the cookie as JSON")
    args = parser.parse_args()

    response = httpx2.get(
        f"{args.base_url.rstrip('/')}/dashboard/api/auth/dev-login",
        follow_redirects=False,
        timeout=30.0,
    )
    if response.status_code == 404:
        raise SystemExit("dev-login is off: set GITHUB_DEV_TOKEN and leave GITHUB_APP_ID unset")
    if response.status_code not in (302, 307):
        raise SystemExit(f"dev-login failed ({response.status_code}): {response.text[:200]}")
    cookie = response.cookies.get(COOKIE_NAME)
    if not cookie:
        raise SystemExit("dev-login set no session cookie")

    if args.json:
        print(json.dumps({"name": COOKIE_NAME, "value": cookie}))
    elif args.curl:
        print(f"--cookie '{COOKIE_NAME}={cookie}'")
    else:
        print(cookie)
    return 0


if __name__ == "__main__":
    sys.exit(main())
