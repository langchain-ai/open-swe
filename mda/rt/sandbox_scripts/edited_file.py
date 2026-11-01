"""Identify the GitHub checkout containing one explicitly edited file."""

import base64
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Tuple


class EditedCheckout:
    def __init__(self, path: str) -> None:
        self.path = Path(path).resolve()

    def git(self, directory: Path, args: Tuple[str, ...]) -> str:
        return (
            subprocess.check_output(
                ["git", "--no-optional-locks", "-C", str(directory), *args],
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            .decode()
            .strip()
        )

    def identify(self) -> Dict[str, str]:
        directory = self.path.parent
        root = Path(self.git(directory, ("rev-parse", "--show-toplevel"))).resolve()
        relative = self.path.relative_to(root).as_posix()
        if ".git" in Path(relative).parts:
            return {}
        remote = self.git(root, ("remote", "get-url", "origin"))
        match = re.fullmatch(
            r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?",
            remote,
        )
        if match is None:
            return {}
        if (
            subprocess.run(
                ["git", "-C", str(root), "check-ignore", "-q", "--", relative],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).returncode
            == 0
        ):
            return {}
        return {
            "repository": match.group(1),
            "path": relative,
            "branch": self.git(root, ("branch", "--show-current")),
        }


try:
    request = json.loads(base64.b64decode(sys.argv[1]))
    result = EditedCheckout(request["path"]).identify()
except (OSError, ValueError, subprocess.SubprocessError):
    result = {}
print(json.dumps(result))
