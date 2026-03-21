from __future__ import annotations

from pathlib import Path

import local_fix_agent as lfa


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def build_plan(tmp_path: Path, script_rel: str, content: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    script_path = repo / script_rel
    write_file(script_path, content)
    return lfa.build_script_validation_plan(repo, script_path)


def test_argparse_script_discovers_help(tmp_path: Path) -> None:
    plan = build_plan(
        tmp_path,
        "tool.py",
        """
import argparse

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name")
    parser.parse_args()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
""".strip()
        + "\n",
    )

    commands = [candidate["command"] for candidate in plan["candidates"]]
    assert "python tool.py --help" in commands
    assert plan["primary_command"] == "python tool.py --help"


def test_nearby_pytest_target_is_preferred(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script_path = repo / "tool.py"
    write_file(
        script_path,
        """
def normalize(value: str) -> str:
    return value.strip().lower()
""".strip()
        + "\n",
    )
    write_file(
        repo / "tests" / "test_tool.py",
        """
from tool import normalize

def test_normalize() -> None:
    assert normalize(" A ") == "a"
""".strip()
        + "\n",
    )

    plan = lfa.build_script_validation_plan(repo, script_path)

    assert plan["primary_command"] == "pytest tests/test_tool.py -q"
    assert plan["chosen_stack"][1]["kind"] == "pytest"


def test_pure_helper_function_enables_function_validation(tmp_path: Path) -> None:
    plan = build_plan(
        tmp_path,
        "helpers.py",
        """
def add(a: int, b: int) -> int:
    return a + b
""".strip()
        + "\n",
    )

    function_validation = plan["function_validation"]
    assert function_validation["considered"] is True
    assert function_validation["used"] is True
    assert function_validation["functions"] == ["add"]
    assert any(step["kind"] == "function" for step in plan["chosen_stack"])


def test_side_effect_function_skips_function_validation(tmp_path: Path) -> None:
    plan = build_plan(
        tmp_path,
        "writer.py",
        """
def write_data(path: str, content: str) -> None:
    with open(path, "w") as handle:
        handle.write(content)
""".strip()
        + "\n",
    )

    function_validation = plan["function_validation"]
    assert function_validation["used"] is False
    assert "No high-confidence pure helper functions found" in function_validation["reason"]


def test_no_runtime_clues_falls_back_to_syntax_and_import(tmp_path: Path) -> None:
    plan = build_plan(
        tmp_path,
        "constants.py",
        """
VALUE = 3
NAME = "demo"
""".strip()
        + "\n",
    )

    assert plan["limited_validation"] is True
    assert plan["only_syntax_import_validation"] is True
    assert plan["primary_command"].startswith("python -c ")
    assert [step["kind"] for step in plan["chosen_stack"]] == ["syntax", "import"]
