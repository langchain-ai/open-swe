"""Harbor eval path normalization for virtualMode rootDir=/app."""

from __future__ import annotations

from pathlib import Path

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend

from managed_deepagents._eval_filesystem import (
    create_eval_trial_filesystem_backend,
    normalize_eval_virtual_path,
)
from managed_deepagents._memory.context_io import result_value
from managed_deepagents._memory.selected_skills import SelectedSkillsBackend


def test_normalize_keeps_virtual_paths() -> None:
    assert normalize_eval_virtual_path("/answer.txt", "/app") == "/answer.txt"
    assert normalize_eval_virtual_path("answer.txt", "/app") == "/answer.txt"


def test_normalize_strips_app_dir_prefix() -> None:
    assert normalize_eval_virtual_path("/app/answer.txt", "/app") == "/answer.txt"
    assert normalize_eval_virtual_path("/app", "/app") == "/"
    assert normalize_eval_virtual_path("/app/", "/app") == "/"
    assert normalize_eval_virtual_path("/app/answer.txt", "/app/") == "/answer.txt"


def test_normalize_collapses_repeated_leading_slashes() -> None:
    assert normalize_eval_virtual_path("//skills/hidden/SKILL.md", "/app") == (
        "/skills/hidden/SKILL.md"
    )
    assert normalize_eval_virtual_path("//app/skills/hidden/SKILL.md", "/app") == (
        "/skills/hidden/SKILL.md"
    )
    assert normalize_eval_virtual_path("/app//skills/hidden/SKILL.md", "/app") == (
        "/skills/hidden/SKILL.md"
    )


def test_create_backend_does_not_double_app_prefix() -> None:
    class _FakeFilesystemBackend:
        def __init__(self, *, root_dir: str | None = None, virtual_mode: bool = False) -> None:
            self.root_dir = root_dir
            self.virtual_mode = virtual_mode

        def _resolve_path(self, key: str) -> str:
            root = self.root_dir or ""
            if self.virtual_mode:
                vpath = key if key.startswith("/") else f"/{key}"
                return f"{root}/{vpath[1:]}".replace("//", "/")
            return key

    backend = create_eval_trial_filesystem_backend(_FakeFilesystemBackend, "/app")
    assert backend._resolve_path("/app/answer.txt") == backend._resolve_path("/answer.txt")
    assert backend._resolve_path("/answer.txt") == "/app/answer.txt"


def test_filtered_backend_hides_skills_from_aliases_and_root_searches(tmp_path: Path) -> None:
    hidden = tmp_path / "skills" / "hidden" / "SKILL.md"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("secret skill")
    (tmp_path / "answer.txt").write_text("visible answer")
    backend = create_eval_trial_filesystem_backend(
        FilesystemBackend,
        str(tmp_path),
        hide_skills=True,
    )

    listed = result_value(backend.ls("/"), "entries")
    assert [result_value(item, "path") for item in listed] == ["/answer.txt"]
    globbed = result_value(backend.glob("**/*", "/"), "matches")
    assert [result_value(item, "path") for item in globbed] == ["/answer.txt"]
    assert result_value(backend.grep("secret", "/"), "matches") == []
    assert result_value(backend.read(str(hidden)), "error") == "file_not_found"
    assert result_value(backend.read(f"{tmp_path}/./skills/hidden/SKILL.md"), "error") == (
        "file_not_found"
    )
    assert result_value(backend.read("//skills/hidden/SKILL.md"), "error") == "file_not_found"
    assert result_value(backend.read(f"/{hidden}"), "error") == "file_not_found"
    assert result_value(backend.write(str(tmp_path / "skills" / "new.md"), "no"), "error") == (
        "permission_denied"
    )
    assert result_value(result_value(backend.read("/answer.txt"), "file_data"), "content") == (
        "visible answer"
    )

    composite = CompositeBackend(
        default=backend,
        routes={"/skills/": SelectedSkillsBackend({"/skills/chosen/SKILL.md": "selected skill"})},
    )
    assert result_value(composite.grep("secret", "/"), "matches") == []
    selected = result_value(composite.grep("selected", "/"), "matches")
    assert [result_value(item, "path") for item in selected] == ["/skills/chosen/SKILL.md"]
    assert result_value(composite.read(str(hidden)), "error") == "file_not_found"
    assert result_value(composite.read("/skills/hidden/SKILL.md"), "error") == "file_not_found"

    empty_composite = CompositeBackend(
        default=backend,
        routes={"/skills/": SelectedSkillsBackend({})},
    )
    assert result_value(empty_composite.read("//skills/hidden/SKILL.md"), "error") == (
        "file_not_found"
    )
    downloaded = empty_composite.download_files(["//skills/hidden/SKILL.md"])[0]
    assert result_value(downloaded, "error") == "file_not_found"
    disk_alias = f"{tmp_path}//skills/hidden/SKILL.md"
    assert result_value(empty_composite.read(disk_alias), "error") == "file_not_found"
    downloaded = empty_composite.download_files([disk_alias])[0]
    assert result_value(downloaded, "error") == "file_not_found"
