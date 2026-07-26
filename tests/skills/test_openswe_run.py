from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SKILL = ROOT / ".claude/skills/openswe-run"
WAVE_SKILL = ROOT / ".claude/skills/openswe-wave"
SCRIPT_PATH = SKILL / "scripts/openswe-run"

# The script is deliberately extensionless (it is the skill's CLI entry point),
# so spec_from_file_location cannot infer a loader for it.
LOADER = importlib.machinery.SourceFileLoader("openswe_run", str(SCRIPT_PATH))
SPEC = importlib.util.spec_from_loader("openswe_run", LOADER)
assert SPEC is not None
run = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run
LOADER.exec_module(run)


def test_entry_point_is_executable_and_self_describes() -> None:
    result = subprocess.run(
        [str(SCRIPT_PATH), "--help"], text=True, capture_output=True, check=False
    )

    assert SCRIPT_PATH.stat().st_mode & 0o111
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()


def test_every_reference_named_in_skill_md_resolves() -> None:
    """A doc that points at a missing reference is a broken skill, not a typo.

    Tracked references live here; the adjudication checklist resolves from the
    sibling openswe-wave, the same fallback the scripts use.
    """
    skill_md = (SKILL / "SKILL.md").read_text()

    for name in sorted(set(re.findall(r"references/([A-Za-z0-9._-]+\.md)", skill_md))):
        local = SKILL / "references" / name
        sibling = WAVE_SKILL / "references" / name
        assert local.is_file() or sibling.is_file(), (
            f"SKILL.md names references/{name}, which exists neither here nor in openswe-wave"
        )


def test_wave_assets_resolve_from_the_sibling_skill_in_a_checkout() -> None:
    """The wave assets come from the sibling skill; without this fallback the
    skill is unrunnable from a checkout."""
    assert not (SKILL / "scripts/openswe_wave.py").exists()

    resolved = run.wave_scripts_dir()

    # wave_scripts_dir() derives from Path(__file__).resolve(); compare resolved
    # paths so a checkout reached through a symlink (macOS /tmp) still matches.
    assert resolved.resolve() == (WAVE_SKILL / "scripts").resolve()
    assert (resolved / "openswe_wave.py").is_file()
    assert (resolved / "wave-monitor").is_file()


def test_wave_symbols_the_script_calls_still_exist() -> None:
    """Guards against a rename in openswe-wave silently breaking this skill."""
    wave = run.import_wave_module()

    assert callable(wave.derive_linear_thread_id)


@pytest.mark.parametrize(
    "body",
    [
        "@openswe repo owner/name — Execute ABC-1 only. See <https://example.com>.",
        "@openswe plain body with no placeholders",
    ],
)
def test_placeholder_guard_allows_filled_bodies(body: str) -> None:
    run.guard_placeholders("ABC-1", body, False)


def test_placeholder_guard_rejects_unfilled_bodies() -> None:
    with pytest.raises(run.RunError, match="placeholder"):
        run.guard_placeholders("ABC-1", "@openswe Execute <TICKET> only.", False)


def test_dispatch_template_leaves_no_placeholder_its_own_guard_would_reject() -> None:
    body = run.DISPATCH_TEMPLATE.format(
        repo="owner/name",
        ticket="ABC-1",
        ref="main",
        scope="do the thing",
        boundaries="nothing else",
        verify="focused tests",
    )

    run.guard_placeholders("ABC-1", body, False)


def test_locked_plan_statuses_match_the_products_refusals() -> None:
    """The dashboard refuses these; this path must not be more permissive."""
    assert set(run.PLAN_STATUS_LOCKED) == {"shared", "cancelled"}
    assert run.PLAN_STATUS_LOCKED[0] in run.PLAN_STATUS_SNIPPET
    assert run.PLAN_STATUS_LOCKED[1] in run.PLAN_STATUS_SNIPPET


def test_no_operator_home_path_is_hardcoded_in_the_source() -> None:
    """This repo is public. The resolved value contains a home path by design;
    what must not appear is a literal one baked into the source."""
    assert run.DEFAULT_STABLE_ROOT.startswith(str(Path.home()))

    for path in (SCRIPT_PATH, SKILL / "SKILL.md"):
        assert not re.search(r"/(Users|home)/[a-z]", path.read_text()), (
            f"{path.name} hardcodes an operator home directory"
        )
