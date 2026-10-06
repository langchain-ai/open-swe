"""Offline behavioral tests for the sandbox publication and browser boundaries.

Also runnable with Python's standard-library unittest when project dependencies
are unavailable: python -m unittest discover -s tests/docs -p test_sandbox_utilities.py
"""

import base64
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.docs.browser import public_addresses
from agent.docs.collect_changes import collect, validate_path


class SandboxPublicationTests(unittest.TestCase):
    def test_allowed_doc_and_forbidden_automation_paths(self) -> None:
        for path in ("guide.mdx", "images/example.png", "navigation.json"):
            validate_path(path)
        for path in (
            "../outside.md",
            "/outside.md",
            ".github/workflows/deploy.yml",
            ".env",
            "guide/AGENTS.md",
            "credentials/key.txt",
            "docs/setup.py",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_path(path)

    def test_exports_edits_new_files_and_deletions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args: str) -> str:
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

            git("init", "-q")
            # Build a base commit using plumbing, without staging/committing any user checkout.
            blob = (
                subprocess.check_output(
                    ["git", "-C", directory, "hash-object", "-w", "--stdin"], input=b"before\n"
                )
                .decode()
                .strip()
            )
            tree = (
                subprocess.check_output(
                    ["git", "-C", directory, "mktree"],
                    input=f"100644 blob {blob}\tguide.md\n100644 blob {blob}\tremoved.md\n".encode(),
                )
                .decode()
                .strip()
            )
            commit = subprocess.check_output(
                [
                    "git",
                    "-C",
                    directory,
                    "-c",
                    "user.name=Docs test",
                    "-c",
                    "user.email=docs-test@example.invalid",
                    "commit-tree",
                    tree,
                    "-m",
                    "fixture",
                ],
                text=True,
            ).strip()
            git("update-ref", "HEAD", commit)
            git("read-tree", "--reset", "-u", "HEAD")
            (root / "guide.md").write_text("after\n")
            (root / "removed.md").unlink()
            (root / "new.mdx").write_text("new page\n")
            changes = {change["path"]: change["content"] for change in collect(root, commit)}
            self.assertEqual(base64.b64decode(changes["guide.md"]), b"after\n")
            self.assertEqual(base64.b64decode(changes["new.mdx"]), b"new page\n")
            self.assertIsNone(changes["removed.md"])
            (root / "symlink.md").symlink_to(root / "guide.md")
            with self.assertRaisesRegex(ValueError, "symlinks"):
                collect(root, commit)

    def test_public_browser_destinations_reject_mixed_and_mapped_private_dns(self) -> None:
        def resolved(*addresses: str) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses
            ]

        with patch("agent.docs.browser.socket.getaddrinfo", return_value=resolved("1.1.1.1")):
            self.assertEqual(public_addresses("docs.example.com"), ["1.1.1.1"])
        for addresses in (
            ("127.0.0.1",),
            ("1.1.1.1", "10.0.0.1"),
            ("::ffff:127.0.0.1",),
            ("169.254.169.254",),
        ):
            with (
                self.subTest(addresses=addresses),
                patch("agent.docs.browser.socket.getaddrinfo", return_value=resolved(*addresses)),
                self.assertRaises(ValueError),
            ):
                public_addresses("docs.example.com")
