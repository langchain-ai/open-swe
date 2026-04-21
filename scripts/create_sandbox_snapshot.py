"""Create a LangSmith sandbox snapshot for open-swe."""

import argparse
import os

from langsmith.sandbox import SandboxClient

DEFAULT_IMAGE = "bracelangchain/deepagents-sandbox:v1"
DEFAULT_FS_CAPACITY = 32 * 1024**3  # 32 GiB


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a LangSmith sandbox snapshot")
    parser.add_argument(
        "--name", default="open-swe-new", help="Snapshot name (default: open-swe-new)"
    )
    parser.add_argument(
        "--image", default=DEFAULT_IMAGE, help=f"Docker image (default: {DEFAULT_IMAGE})"
    )
    parser.add_argument(
        "--fs-capacity",
        type=int,
        default=DEFAULT_FS_CAPACITY,
        help="FS capacity in bytes (default: 32 GiB)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD"),
        help="LangSmith API key",
    )
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("Set LANGSMITH_API_KEY or pass --api-key")

    client = SandboxClient(api_key=args.api_key)
    snapshot = client.create_snapshot(
        name=args.name,
        docker_image=args.image,
        fs_capacity_bytes=args.fs_capacity,
    )
    print(f"Snapshot created: {snapshot.id}")
    print(f"\nAdd to your .env:\n  DEFAULT_SANDBOX_SNAPSHOT_ID={snapshot.id}")


if __name__ == "__main__":
    main()
