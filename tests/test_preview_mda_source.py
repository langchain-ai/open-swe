from pathlib import Path

import pytest

from scripts.preview_mda_source import SourceError, selected_source

APPROVED = "8018345eb6a4c5cd19305b0651f4643f5fe6f694"
SDK = "https://github.com/langchain-ai/managed-deepagents-sdk"


def _pyproject(tmp_path: Path, source: str | None, name: str = "managed-deepagents") -> Path:
    path = tmp_path / "pyproject.toml"
    sources = f"\n[tool.uv.sources]\n{name} = {source}\n" if source else ""
    path.write_text(
        '[project]\nname = "open-swe-runtime"\ndependencies = ["managed-deepagents==0.9.2"]\n'
        + sources
    )
    return path


def test_a_tree_without_an_sdk_source_deploys_with_the_published_cli(tmp_path: Path) -> None:
    assert selected_source(_pyproject(tmp_path, None), APPROVED) is None


def test_an_approved_sdk_commit_builds_its_own_compiler(tmp_path: Path) -> None:
    source = f'{{ git = "{SDK}", rev = "{APPROVED}", subdirectory = "packages/pypi" }}'
    assert selected_source(_pyproject(tmp_path, source), APPROVED) == APPROVED


@pytest.mark.parametrize(
    ("source", "approved"),
    [
        pytest.param(
            f'{{ git = "{SDK}", rev = "{APPROVED}", subdirectory = "packages/pypi" }}',
            "",
            id="nothing-approved",
        ),
        pytest.param(
            f'{{ git = "{SDK}", rev = "{"0" * 40}", subdirectory = "packages/pypi" }}',
            APPROVED,
            id="another-commit",
        ),
        pytest.param(
            f'{{ git = "{SDK}", rev = "{APPROVED[:12]}", subdirectory = "packages/pypi" }}',
            APPROVED[:12],
            id="short-sha",
        ),
        pytest.param(
            f'{{ git = "{SDK}", rev = "main", subdirectory = "packages/pypi" }}',
            "main",
            id="branch",
        ),
        pytest.param(
            f'{{ git = "https://github.com/someone/managed-deepagents-sdk", rev = "{APPROVED}", '
            'subdirectory = "packages/pypi" }',
            APPROVED,
            id="fork",
        ),
        pytest.param(
            f'{{ git = "{SDK}", rev = "{APPROVED}", subdirectory = "packages/npm" }}',
            APPROVED,
            id="other-package",
        ),
        pytest.param(
            f'{{ git = "{SDK}", branch = "main", rev = "{APPROVED}", subdirectory = "packages/pypi" }}',
            APPROVED,
            id="extra-keys",
        ),
        pytest.param('{ path = "../managed-deepagents-sdk/packages/pypi" }', APPROVED, id="path"),
    ],
)
@pytest.mark.parametrize(
    "name", ["managed-deepagents", "managed_deepagents", '"Managed.DeepAgents"']
)
def test_the_preview_builds_no_unapproved_or_foreign_mda_source(
    tmp_path: Path, source: str, approved: str, name: str
) -> None:
    with pytest.raises(SourceError):
        selected_source(_pyproject(tmp_path, source, name), approved)
