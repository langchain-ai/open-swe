import argparse
import json
import logging
from pathlib import Path


def load_aliases(path: str) -> dict[str, str]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--config", default="")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    aliases = load_aliases(args.config) if args.config else {}
    value = aliases.get(args.name, args.name).strip().lower().replace(" ", "-")
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
