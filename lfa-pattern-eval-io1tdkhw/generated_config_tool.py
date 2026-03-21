"""Create a small local CLI utility that normalizes names and supports alias lookup from a JSON config file."""
import argparse
import json
from pathlib import Path

def load_aliases(path: str) -> dict[str, str]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text())

def normalize_name(value: str) -> str:
    return "-".join(part for part in value.strip().lower().replace("_", "-").split("-") if part)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("value")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--config", default="")
    args = parser.parse_args()
    aliases = load_aliases(args.config) if args.config else {}
    value = aliases.get(args.value, args.value)
    normalized = normalize_name(args.value)
    normalized = normalize_name(value)
    if args.prefix:
        normalized = normalize_name(args.prefix) + '-' + normalized
    print(normalized)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
