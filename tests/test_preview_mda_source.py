from pathlib import Path

import pytest

from scripts.preview_mda_source import SourceError, selected_source

SHA = "8018345eb6a4c5cd19305b0651f4643f5fe6f694"
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
    assert selected_source(_pyproject(tmp_path, None)) is None


@pytest.mark.parametrize("sha", [SHA, "a" * 40])
def test_the_tree_selects_its_sdk_commit_without_separate_approval(
    tmp_path: Path, sha: str
) -> None:
    source = f'{{ git = "{SDK}", rev = "{sha}", subdirectory = "packages/pypi" }}'
    assert selected_source(_pyproject(tmp_path, source)) == sha


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            f'{{ git = "{SDK}", rev = "{SHA[:12]}", subdirectory = "packages/pypi" }}',
            id="short-sha",
        ),
        pytest.param(
            f'{{ git = "{SDK}", rev = "{SHA.upper()}", subdirectory = "packages/pypi" }}',
            id="uppercase-sha",
        ),
        pytest.param(
            f'{{ git = "{SDK}", rev = "main", subdirectory = "packages/pypi" }}',
            id="branch",
        ),
        pytest.param(
            f'{{ git = "https://github.com/someone/managed-deepagents-sdk", rev = "{SHA}", '
            'subdirectory = "packages/pypi" }',
            id="fork",
        ),
        pytest.param(
            f'{{ git = "{SDK}", rev = "{SHA}", subdirectory = "packages/npm" }}',
            id="other-package",
        ),
        pytest.param(
            f'{{ git = "{SDK}", branch = "main", rev = "{SHA}", subdirectory = "packages/pypi" }}',
            id="extra-keys",
        ),
        pytest.param('{ path = "../managed-deepagents-sdk/packages/pypi" }', id="path"),
    ],
)
@pytest.mark.parametrize(
    "name", ["managed-deepagents", "managed_deepagents", '"Managed.DeepAgents"']
)
def test_the_preview_builds_no_invalid_or_foreign_mda_source(
    tmp_path: Path, source: str, name: str
) -> None:
    with pytest.raises(SourceError):
        selected_source(_pyproject(tmp_path, source, name))
