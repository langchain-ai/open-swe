"""Create a small local CLI utility that normalizes names and supports alias lookup from a JSON config file."""
import argparse

def normalize_name(value: str) -> str:
    return "-".join(part for part in value.strip().lower().replace("_", "-").split("-") if part)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("value")
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()
    value = args.value
    normalized = normalize_name(args.value)
    if args.prefix:
        normalized = normalize_name(args.prefix) + '-' + normalized
    print(normalized)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
