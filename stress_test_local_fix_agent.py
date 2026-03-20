from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
LOCAL_FIX_AGENT = SCRIPT_DIR / "local_fix_agent.py"


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str
    description: str
    files: dict[str, str]
    test_cmd: str = "pytest -q"


def scenario_definitions() -> list[Scenario]:
    return [
        Scenario(
            name="syntax_error",
            kind="syntax error",
            description="Broken function definition that should be fixed with a minimal edit.",
            files={
                "calculator.py": """
                def add(a, b)
                    return a + b
                """,
                "tests/test_calculator.py": """
                from calculator import add


                def test_add():
                    assert add(2, 3) == 5
                """,
            },
        ),
        Scenario(
            name="import_error",
            kind="import error",
            description="Implementation imports the wrong helper module.",
            files={
                "helpers.py": """
                def normalize_name(value: str) -> str:
                    return value.strip().title()
                """,
                "greeter.py": """
                from helper import normalize_name


                def greet(name: str) -> str:
                    return f"Hello, {normalize_name(name)}!"
                """,
                "tests/test_greeter.py": """
                from greeter import greet


                def test_greet_formats_name():
                    assert greet("  ada lovelace ") == "Hello, Ada Lovelace!"
                """,
            },
        ),
        Scenario(
            name="assertion_failure",
            kind="assertion failure",
            description="Function returns the wrong business value.",
            files={
                "discounts.py": """
                def loyalty_discount(points: int) -> int:
                    if points >= 100:
                        return 5
                    return 0
                """,
                "tests/test_discounts.py": """
                from discounts import loyalty_discount


                def test_loyalty_discount_for_vip_customer():
                    assert loyalty_discount(150) == 10
                """,
            },
        ),
        Scenario(
            name="runtime_error",
            kind="runtime error",
            description="Function crashes on optional data instead of handling a default.",
            files={
                "payloads.py": """
                def extract_count(payload: dict) -> int:
                    return int(payload["count"])
                """,
                "tests/test_payloads.py": """
                from payloads import extract_count


                def test_extract_count_defaults_to_zero():
                    assert extract_count({}) == 0
                """,
            },
        ),
        Scenario(
            name="stagnation_trap",
            kind="repeated failure / stagnation",
            description="The failing behavior is driven by a default constant instead of the obvious wrapper function.",
            files={
                "defaults.py": """
                UNKNOWN_LABEL = "missing"
                """,
                "reports.py": """
                from defaults import UNKNOWN_LABEL


                def report_label(record: dict) -> str:
                    return record.get("label", UNKNOWN_LABEL)
                """,
                "tests/test_reports.py": """
                from reports import report_label


                def test_report_label_uses_unknown_default():
                    assert report_label({}) == "unknown"
                """,
            },
        ),
        Scenario(
            name="wrong_file_temptation",
            kind="wrong-file temptation",
            description="A helper already works; the wrapper is the real bug.",
            files={
                "slugify.py": """
                def slugify(value: str) -> str:
                    return value.strip().lower().replace(" ", "-")
                """,
                "endpoints.py": """
                from slugify import slugify


                def article_path(title: str) -> str:
                    return f"/articles/{title}"
                """,
                "tests/test_endpoints.py": """
                from endpoints import article_path


                def test_article_path_uses_slugified_title():
                    assert article_path("Hello World") == "/articles/hello-world"
                """,
            },
        ),
    ]


def dedent_files(files: dict[str, str]) -> dict[str, str]:
    return {path: textwrap.dedent(content).lstrip("\n") for path, content in files.items()}


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n")


def run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def build_repo(root: Path, scenario: Scenario) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    for rel_path, content in dedent_files(scenario.files).items():
        write_text(root / rel_path, content)

    for command in [
        ["git", "init"],
        ["git", "config", "user.email", "stress@example.com"],
        ["git", "config", "user.name", "Stress Harness"],
        ["git", "add", "."],
        ["git", "commit", "-m", "baseline scenario"],
    ]:
        completed = run_command(command, root)
        if completed.returncode != 0:
            raise RuntimeError(f"Failed to run {' '.join(command)}:\n{completed.stdout}\n{completed.stderr}")


def parse_bool(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.M) is not None


def last_match(pattern: str, text: str) -> str:
    matches = re.findall(pattern, text, re.M)
    return matches[-1] if matches else ""


def parse_commit_outcome(log_text: str) -> str:
    marker = "=== AUTO-COMMIT RESULT ==="
    if marker not in log_text:
        return "not_reached"
    tail = log_text.split(marker, 1)[1]
    if '"skipped": true' in tail:
        return "skipped"
    if '"ok": true' in tail:
        return "performed"
    return "failed"


def parse_best_attempt(log_text: str) -> dict[str, str]:
    match = re.findall(
        r"Best attempt so far: step (\d+) score (-?\d+) failure_type=([\w_]+) files=(\d+)",
        log_text,
    )
    if not match:
        return {}
    step, score, failure_type, files = match[-1]
    return {
        "step": step,
        "score": score,
        "failure_type": failure_type,
        "files": files,
    }


def parse_run_summary(log_text: str, exit_code: int) -> dict[str, object]:
    attempt_count = len(re.findall(r"^=== AGENT STEP \d+ ===$", log_text, re.M))
    final_strategy = last_match(r"Active strategy mode: ([\w_]+)", log_text)
    failure_type = last_match(r"Detected failure type: ([\w_]+)", log_text)
    final_score = last_match(r"Attempt score: (-?\d+)", log_text)
    best_attempt = parse_best_attempt(log_text)
    return {
        "tests_passed": exit_code == 0 and "=== FINAL RESPONSE ===" in log_text,
        "attempts": attempt_count,
        "final_strategy_mode": final_strategy,
        "detected_failure_type": failure_type,
        "rollback_triggered": parse_bool(r"=== AUTO-ROLLBACK ===", log_text),
        "broader_rewrite_triggered": parse_bool(r"Active strategy mode: broader_rewrite", log_text),
        "search_repo_triggered": parse_bool(r"Tool call: search_repo\(", log_text),
        "commit_outcome": parse_commit_outcome(log_text),
        "final_score": int(final_score) if final_score else None,
        "best_attempt": best_attempt,
    }


def run_scenario(
    scenario: Scenario,
    work_root: Path,
    max_steps: int,
    agent_path: Path,
) -> dict[str, object]:
    scenario_root = work_root / "repos" / scenario.name
    logs_root = work_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_path = logs_root / f"{scenario.name}.log"
    detail_path = logs_root / f"{scenario.name}.json"

    build_repo(scenario_root, scenario)

    command = [
        sys.executable,
        str(agent_path),
        "--repo",
        str(scenario_root),
        "--test-cmd",
        scenario.test_cmd,
        "--max-steps",
        str(max_steps),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    log_text = completed.stdout + ("\n" + completed.stderr if completed.stderr else "")
    log_path.write_text(log_text)

    summary = parse_run_summary(log_text, completed.returncode)
    summary.update(
        {
            "scenario": scenario.name,
            "kind": scenario.kind,
            "description": scenario.description,
            "log_path": str(log_path),
            "repo_path": str(scenario_root),
            "exit_code": completed.returncode,
        }
    )
    detail_path.write_text(json.dumps(summary, indent=2))
    return summary


def format_table(rows: list[dict[str, object]]) -> str:
    headers = [
        "scenario",
        "passed",
        "attempts",
        "strategy",
        "failure",
        "rollback",
        "rewrite",
        "search",
        "commit",
        "score",
        "best",
    ]
    rendered = []
    for row in rows:
        best = row.get("best_attempt") or {}
        rendered.append(
            {
                "scenario": str(row.get("scenario", "")),
                "passed": "yes" if row.get("tests_passed") else "no",
                "attempts": str(row.get("attempts", "")),
                "strategy": str(row.get("final_strategy_mode", "")),
                "failure": str(row.get("detected_failure_type", "")),
                "rollback": "yes" if row.get("rollback_triggered") else "no",
                "rewrite": "yes" if row.get("broader_rewrite_triggered") else "no",
                "search": "yes" if row.get("search_repo_triggered") else "no",
                "commit": str(row.get("commit_outcome", "")),
                "score": "" if row.get("final_score") is None else str(row.get("final_score")),
                "best": (
                    f"s{best.get('step','')}/sc{best.get('score','')}"
                    if best
                    else ""
                ),
            }
        )

    widths = {header: len(header) for header in headers}
    for row in rendered:
        for header in headers:
            widths[header] = max(widths[header], len(row[header]))

    lines = []
    lines.append(" ".join(header.ljust(widths[header]) for header in headers))
    lines.append(" ".join("-" * widths[header] for header in headers))
    for row in rendered:
        lines.append(" ".join(row[header].ljust(widths[header]) for header in headers))
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", default=f"/tmp/local-fix-agent-stress-{int(time.time())}")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--agent-path", default=str(LOCAL_FIX_AGENT))
    parser.add_argument("--scenarios", nargs="*", default=[])
    parser.add_argument("--list-scenarios", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scenarios = scenario_definitions()
    scenario_map = {scenario.name: scenario for scenario in scenarios}

    if args.list_scenarios:
        for scenario in scenarios:
            print(f"{scenario.name}: {scenario.kind} - {scenario.description}")
        return

    if args.scenarios:
        selected = []
        for name in args.scenarios:
            if name not in scenario_map:
                raise SystemExit(f"Unknown scenario: {name}")
            selected.append(scenario_map[name])
    else:
        selected = scenarios

    work_root = Path(args.work_root).expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)

    results = []
    for scenario in selected:
        print(f"\n=== SCENARIO {scenario.name} ===")
        print(f"Kind: {scenario.kind}")
        print(f"Description: {scenario.description}")
        summary = run_scenario(scenario, work_root, args.max_steps, Path(args.agent_path).resolve())
        results.append(summary)
        print(
            f"Result: passed={summary['tests_passed']} attempts={summary['attempts']} "
            f"strategy={summary['final_strategy_mode']} failure={summary['detected_failure_type']} "
            f"score={summary['final_score']}"
        )
        print(f"Log: {summary['log_path']}")

    summary_path = work_root / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2))

    print("\n=== SUMMARY TABLE ===")
    print(format_table(results))
    print(f"\nSummary JSON: {summary_path}")


if __name__ == "__main__":
    main()
