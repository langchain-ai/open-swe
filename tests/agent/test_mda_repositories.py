"""``mda/rt/repositories.py`` reads a repository from text as the backend does.

MDA's Linear handler keeps its own copy because ``mda/`` never imports the backend
(AGENTS.md); the backend checks the repository again before it signs a run, so a
drift would send people the wrong decline rather than reach a repository, but it
must not drift. It is standard library only so this test can load it.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from openswe.utils.repo import extract_repo_from_text


def _mda_copy() -> ModuleType:
    path = Path(__file__).parents[2] / "mda" / "rt" / "repositories.py"
    spec = importlib.util.spec_from_file_location("mda_repositories", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MDA = _mda_copy()


@pytest.mark.parametrize(
    "text",
    [
        "fix it in repo:acme/web",
        "fix it in repo web",
        "see https://github.com/langchain-ai/langgraph-api but use repo:my-org/my-repo",
        "Fix the repo setup. It crashes in repo:acme/web on login.",
        "![shot](https://github.com/user-attachments/assets/0b1c) "
        "https://gist.github.com/ada/123 https://github.com/acme/app/issues/7",
        "no repository here",
    ],
)
@pytest.mark.parametrize("explicit_only", [False, True])
def test_the_copy_reads_the_repository_the_backend_reads(text: str, explicit_only: bool) -> None:
    repo = extract_repo_from_text(text, "acme", explicit_only=explicit_only)
    expected = f"{repo['owner']}/{repo['name']}" if repo else None
    assert _MDA.repository_in(text, "acme", explicit_only=explicit_only) == expected
