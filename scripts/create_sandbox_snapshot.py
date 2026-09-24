"""Create a LangSmith sandbox snapshot for open-swe."""

import argparse

from langsmith.sandbox import SandboxClient

from agent.config import ENV

DEFAULT_IMAGE = "johanneslangchain/open-swe-sandbox:gh-cli-amd64"
DEFAULT_FS_CAPACITY = 32 * 1024**3  # 32 GiB

# Pinned for reproducible builds; linux-x64 matches the amd64 sandbox image.
OPENVSCODE_VERSION = "1.109.5"
OPENVSCODE_ARCH = "linux-x64"
OPENVSCODE_URL = (
    "https://github.com/gitpod-io/openvscode-server/releases/download/"
    f"openvscode-server-v{OPENVSCODE_VERSION}/"
    f"openvscode-server-v{OPENVSCODE_VERSION}-{OPENVSCODE_ARCH}.tar.gz"
)


def editor_dockerfile(base_image: str) -> str:
    return f"""FROM {base_image}
RUN curl -fsSL {OPENVSCODE_URL} -o /tmp/openvscode-server.tar.gz \\
    && mkdir -p /opt/openvscode/ovs \\
    && tar -xzf /tmp/openvscode-server.tar.gz -C /opt/openvscode/ovs --strip-components=1 \\
    && rm /tmp/openvscode-server.tar.gz
"""


def main() -> None:
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
        "--with-editor",
        action="store_true",
        help="Layer the VS Code server into /opt/openvscode for the editor button",
    )
    parser.add_argument(
        "--api-key",
        default=ENV.LANGSMITH_API_KEY.optional(),
        help="LangSmith API key (default: LANGSMITH_API_KEY)",
    )
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("Set LANGSMITH_API_KEY or pass --api-key")

    client = SandboxClient(api_key=args.api_key)
    if args.with_editor:
        snapshot = client.create_snapshot_from_dockerfile(
            args.name,
            editor_dockerfile(args.image),
            fs_capacity_bytes=args.fs_capacity,
        )
    else:
        snapshot = client.create_snapshot(
            name=args.name,
            docker_image=args.image,
            fs_capacity_bytes=args.fs_capacity,
        )
    print(f"Snapshot created: {snapshot.id}")
    print("\nSet it as a workspace's base snapshot from the dashboard Workspaces page.")


if __name__ == "__main__":
    main()
