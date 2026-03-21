import argparse
import logging
import os
import urllib.request


def build_proxy_map() -> dict[str, str]:
    proxies: dict[str, str] = {}
    for key, scheme in [("HTTP_PROXY", "http"), ("HTTPS_PROXY", "https")]:
        value = os.getenv(key) or os.getenv(key.lower())
        if value:
            proxies[scheme] = value
    return proxies


def build_proxy_opener() -> urllib.request.OpenerDirector:
    handler = urllib.request.ProxyHandler(build_proxy_map())
    return urllib.request.build_opener(handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://example.com")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.info("configured proxy routes: %s", sorted(build_proxy_map()))
    opener = build_proxy_opener()
    if args.url == "print-only":
        print(opener)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
