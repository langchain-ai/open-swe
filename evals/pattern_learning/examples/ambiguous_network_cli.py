import argparse
import logging
import os
import time
import urllib.request


def configured_proxy() -> str:
    return os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or ""


def run_once() -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": configured_proxy()} if configured_proxy() else {}))
    if opener and configured_proxy():
        return "proxy-ok"
    time.sleep(0.01)
    return "local-ok"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="auto")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(run_once() if args.mode else "noop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
