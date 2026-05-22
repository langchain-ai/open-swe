import json
import subprocess
from pathlib import Path

from docs.hermes.pseudo_router.bootstrap_install_policy_parser import (
    EXACT_APPROVAL_PHRASE,
    SCHEMA_VERSION,
    evaluate_bootstrap_install_policy,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_REPO = "ollehillbom1/hermes-open-swe-testrepo"
NORTHSTAR_REPO = "ollehillbom1/north-star-erp"


def test_defaults_to_install_allowed_false_without_approval_text():
    decision = evaluate_bootstrap_install_policy(
        approval_text="",
        target_repo=TEST_REPO,
        allowed_test_repos=[TEST_REPO],
    )

    assert decision["schema_version"] == SCHEMA_VERSION
    assert decision["status"] == "POLICY_EVALUATED"
    assert decision["install_allowed"] is False
    assert decision["INSTALL_ALLOWED"] is False
    assert decision["gate"] == "WARN"
    assert decision["required_exact_approval"] == EXACT_APPROVAL_PHRASE
    assert decision["block_reasons"] == ["missing_exact_approval_phrase"]
    assert decision["side_effects"] == []


def test_allows_only_exact_phrase_plus_allowlisted_testrepo():
    decision = evaluate_bootstrap_install_policy(
        approval_text=f"I approve the dry run testrepo bootstrap.\n{EXACT_APPROVAL_PHRASE}\nProceed for testrepo only.",
        target_repo=TEST_REPO,
        allowed_test_repos=[TEST_REPO],
    )

    assert decision["status"] == "POLICY_EVALUATED"
    assert decision["install_allowed"] is True
    assert decision["INSTALL_ALLOWED"] is True
    assert decision["gate"] == "PASS"
    assert decision["target_repo"] == TEST_REPO
    assert decision["block_reasons"] == []
    assert decision["side_effects"] == []
    assert decision["allowed_actions"] == ["dry_run_testrepo_bootstrap_install"]


def test_near_miss_approval_phrases_do_not_allow_install():
    near_misses = [
        "ALLOW_BOOTSTRAP_INSTALL=yes",
        "allow_bootstrap_install=YES",
        "ALLOW_BOOTSTRAP_INSTALL = YES",
        "ALLOW_BOOTSTRAP_INSTALL=YESPLEASE",
        "ALLOW_BOOTSTRAP_INSTALL=YES; also deploy prod",
    ]

    for approval_text in near_misses:
        decision = evaluate_bootstrap_install_policy(
            approval_text=approval_text,
            target_repo=TEST_REPO,
            allowed_test_repos=[TEST_REPO],
        )

        assert decision["install_allowed"] is False
        assert decision["INSTALL_ALLOWED"] is False
        assert "missing_exact_approval_phrase" in decision["block_reasons"]
        assert decision["side_effects"] == []


def test_blocks_northstar_repo_even_with_exact_approval_unless_explicitly_allowlisted():
    decision = evaluate_bootstrap_install_policy(
        approval_text=EXACT_APPROVAL_PHRASE,
        target_repo=NORTHSTAR_REPO,
        allowed_test_repos=[TEST_REPO],
    )

    assert decision["install_allowed"] is False
    assert decision["INSTALL_ALLOWED"] is False
    assert decision["gate"] == "WARN"
    assert decision["target_repo"] == NORTHSTAR_REPO
    assert decision["block_reasons"] == ["target_repo_not_allowlisted_for_testrepo_bootstrap"]
    assert decision["side_effects"] == []


def test_does_not_echo_human_text_or_secret_like_material():
    approval_text = f"{EXACT_APPROVAL_PHRASE}\nDo not log this token ghp_FAKESECRET1234567890"

    decision = evaluate_bootstrap_install_policy(
        approval_text=approval_text,
        target_repo=TEST_REPO,
        allowed_test_repos=[TEST_REPO],
    )
    serialized = json.dumps(decision, sort_keys=True)

    assert decision["install_allowed"] is False
    assert decision["INSTALL_ALLOWED"] is False
    assert "secret_like_approval_text_detected" in decision["block_reasons"]
    assert "ghp_" not in serialized
    assert "FAKESECRET" not in serialized
    assert approval_text not in serialized
    assert decision["approval_text_echoed"] is False
    assert decision["side_effects"] == []


def test_cli_reads_policy_json_and_writes_only_stdout(tmp_path):
    input_path = tmp_path / "policy_input.json"
    input_path.write_text(
        json.dumps(
            {
                "approval_text": EXACT_APPROVAL_PHRASE,
                "target_repo": TEST_REPO,
                "allowed_test_repos": [TEST_REPO],
            }
        ),
        encoding="utf-8",
    )

    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/bootstrap_install_policy_parser.py"),
            str(input_path),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    output = json.loads(result.stdout)

    assert result.returncode == 0
    assert result.stderr == ""
    assert output["install_allowed"] is True
    assert output["INSTALL_ALLOWED"] is True
    assert output["side_effects"] == []
    assert before == after == ["policy_input.json"]
