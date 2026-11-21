"""Fetch the managed-deepagents SDK for the Agent Server image build (langgraph.json).

pyproject.toml takes managed-deepagents from a pinned commit of the private langchainplus
repository, and the generated install step has no git credentials. A dockerfile_lines step
runs this script with BuildKit secrets mounted:

- LangSmith deployment builds mount each deployment secret, so the step mounts the Robin
  GitHub App's (GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY). The script
  mints an installation token scoped to reading the SDK's repository and revokes it after.
- CI's manifest build (.github/scripts/langgraph_manifests.py) mounts the run's read-only
  token as MDA_SDK_GITHUB_TOKEN, used as is.

It makes a shallow, sparse, blobless checkout of the SDK's Python package at the pinned
commit into /opt/managed-deepagents and writes the uv override file (UV_OVERRIDE) that points
the install at that copy. No token is written to disk, so no image layer keeps one.

Without either secret (a local `langgraph build`), it writes an empty override file and the
install fetches the git source as it would without this step.

    python fetch_mda_sdk.py <pyproject.toml>
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEST = Path("/opt/managed-deepagents")
OVERRIDE = Path("/opt/managed-deepagents-override.txt")


class GitHubApi:
    """The few GitHub REST calls the fetch makes, as one bearer."""

    URL = "https://api.github.com"

    def __init__(self, bearer: str) -> None:
        self.bearer = bearer

    def call(self, method: str, path: str, body: dict[str, object] | None = None) -> dict:
        request = urllib.request.Request(
            self.URL + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.bearer}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}


@dataclass
class Token:
    value: str
    revoke: bool

    def revoke_if_minted(self) -> None:
        if not self.revoke:
            return
        try:
            GitHubApi(self.value).call("DELETE", "/installation/token")
        except OSError as error:
            print(f"fetch_mda_sdk: could not revoke the installation token: {error}")


class BuildSecrets:
    """The BuildKit secrets the step mounts, by id, at their default /run/secrets path."""

    def __init__(self, root: Path = Path("/run/secrets")) -> None:
        self.root = root

    def read(self, name: str) -> str:
        path = self.root / name
        return path.read_text().strip() if path.is_file() else ""

    def token(self, repo: str) -> Token | None:
        if ready := self.read("MDA_SDK_GITHUB_TOKEN"):
            return Token(ready, revoke=False)
        app_id = self.read("GITHUB_APP_ID")
        installation_id = self.read("GITHUB_APP_INSTALLATION_ID")
        # Stored with escaped newlines, as the backend reads it (openswe/github/app.py).
        private_key = self.read("GITHUB_APP_PRIVATE_KEY").replace("\\n", "\n")
        if not (app_id and installation_id and private_key):
            return None
        import jwt  # the Agent Server image ships PyJWT and cryptography

        now = int(time.time())
        app_jwt = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256"
        )
        created = GitHubApi(app_jwt).call(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            {"repositories": [repo], "permissions": {"contents": "read"}},
        )
        return Token(created["token"], revoke=True)


@dataclass
class SdkSource:
    """pyproject.toml's git source for managed-deepagents."""

    url: str
    rev: str
    subdirectory: str

    @classmethod
    def from_pyproject(cls, path: Path) -> SdkSource:
        source = tomllib.loads(path.read_text())["tool"]["uv"]["sources"]["managed-deepagents"]
        return cls(source["git"], source["rev"], source["subdirectory"])

    @property
    def repo(self) -> str:
        return self.url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]

    def fetch(self, token: str, dest: Path) -> None:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        # Passed through the environment, so it reaches neither argv nor a config file.
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        }
        with tempfile.TemporaryDirectory() as checkout:
            for args in (
                ["init", "-q"],
                ["sparse-checkout", "set", self.subdirectory],
                ["fetch", "-q", "--depth", "1", "--filter=blob:none", self.url, self.rev],
                ["-c", "advice.detachedHead=false", "checkout", "-q", "--detach", self.rev],
            ):
                subprocess.run(["git", "-C", checkout, *args], check=True, env=env)
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(Path(checkout) / self.subdirectory, dest)


def main() -> None:
    source = SdkSource.from_pyproject(Path(sys.argv[1]))
    token = BuildSecrets().token(source.repo)
    if token is None:
        print("fetch_mda_sdk: no GitHub build secrets; uv fetches the git source itself")
        OVERRIDE.write_text("")
        return
    try:
        source.fetch(token.value, DEST)
    finally:
        token.revoke_if_minted()
    OVERRIDE.write_text(f"managed-deepagents @ file://{DEST}\n")
    print(f"fetch_mda_sdk: managed-deepagents {source.rev} from {source.url} -> {DEST}")


if __name__ == "__main__":
    main()
