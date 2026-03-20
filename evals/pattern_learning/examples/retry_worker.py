import argparse
import logging
import time


def fetch_with_retry(max_attempts: int, delay_seconds: float) -> str:
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt < max_attempts:
                raise RuntimeError("429 rate limit")
            return "ok"
        except RuntimeError as exc:
            last_error = str(exc)
            logging.warning("attempt %s failed: %s", attempt, exc)
            if "429" in str(exc) and attempt < max_attempts:
                time.sleep(delay_seconds)
    raise RuntimeError(last_error)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.01)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(fetch_with_retry(args.attempts, args.delay))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
