from __future__ import annotations

import pathlib

from deepagents.middleware.skills import _parse_skill_metadata

from agent.dashboard.review_style_jobs import (
    build_continual_run_configurable,
    build_continual_run_input,
)
from agent.utils.analyzer_skills import (
    ANALYZER_MODES,
    SKILLS_DIR,
    build_skill_files,
    skill_path_for_mode,
)


def test_build_skill_files_stripped_keys_and_valid_file_data() -> None:
    files = build_skill_files()
    assert set(files) == {
        "/bootstrap-repo-analysis/SKILL.md",
        "/continual-learning/SKILL.md",
    }
    for entry in files.values():
        assert entry["encoding"] == "utf-8"
        assert isinstance(entry["content"], str) and entry["content"].strip()
        assert "created_at" in entry and "modified_at" in entry


def test_skill_path_for_mode() -> None:
    assert skill_path_for_mode("bootstrap") == "/skills/bootstrap-repo-analysis/SKILL.md"
    assert skill_path_for_mode("continual") == "/skills/continual-learning/SKILL.md"
    # Unknown modes fall back to bootstrap.
    assert skill_path_for_mode("whatever") == "/skills/bootstrap-repo-analysis/SKILL.md"


def test_bundled_skill_md_parse() -> None:
    for skill in ANALYZER_MODES.values():
        path = SKILLS_DIR / skill / "SKILL.md"
        meta = _parse_skill_metadata(path.read_text(), str(path), path.parent.name)
        assert meta["name"] == skill
        assert meta["description"].strip()


def test_skills_dir_resolves() -> None:
    assert SKILLS_DIR.name == "skills"
    assert (SKILLS_DIR / "bootstrap-repo-analysis" / "SKILL.md").exists()
    assert isinstance(SKILLS_DIR, pathlib.Path)


def test_continual_run_payload_carries_mode_and_skill_files() -> None:
    configurable = build_continual_run_configurable("o/r")
    assert configurable["analyzer_mode"] == "continual"
    assert configurable["review_style_full_name"] == "o/r"

    run_input = build_continual_run_input("o/r")
    assert "/continual-learning/SKILL.md" in run_input["files"]
    assert run_input["messages"][0]["role"] == "user"
