"""Create a LangSmith sandbox snapshot for open-swe."""

import argparse

from langsmith.sandbox import SandboxClient

from openswe.sandboxes.providers.langsmith_api import SandboxAPI

DEFAULT_IMAGE = "johanneslangchain/open-swe-sandbox:gh-cli-amd64"
DEFAULT_FS_CAPACITY = 32 * 1024**3  # 32 GiB


def main() -> None:
    api = SandboxAPI.from_env()
    parser = argparse.ArgumentParser(description="Create a LangSmith sandbox snapshot")
    parser.add_argument(
        "--name", default="open-swe-gh-amd64", help="Snapshot name (default: open-swe-gh-amd64)"
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
        default=api.api_key,
        help="LangSmith API key (default: SANDBOX_LANGSMITH_API_KEY, else LANGSMITH_API_KEY)",
    )
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("Set SANDBOX_LANGSMITH_API_KEY or LANGSMITH_API_KEY, or pass --api-key")

    client = SandboxClient(api_key=args.api_key, api_endpoint=api.endpoint)
    snapshot = client.create_snapshot(
        name=args.name,
        docker_image=args.image,
        fs_capacity_bytes=args.fs_capacity,
    )
    print(f"Snapshot created: {snapshot.id}")
    print("\nSet it as a workspace's base snapshot from the dashboard Workspaces page.")


if __name__ == "__main__":
    main()
