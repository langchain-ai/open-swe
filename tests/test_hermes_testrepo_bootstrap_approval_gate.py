from docs.hermes.pseudo_router.testrepo_bootstrap_approval_gate import (
    SCHEMA_VERSION,
    build_bootstrap_approval_packet,
)


def _pipeline_result(**overrides):
    result = {
        "status": "OK",
        "schema_version": "hermes.review-pipeline.v1",
        "gate": "WARN",
        "TASK_ID": "GH-REVIEW-42-9001",
        "source_event": {
            "repo": "ollehillbom1/northstar-agent-harness-testrepo",
            "comment_author": "ollehillbom1",
            "issue_number": 42,
            "comment_id": 9001,
        },
        "stages": {
            "github_comment_adapter": {"status": "OK", "gate": "PASS"},
            "next_action_policy": {"status": "OK", "gate": "WARN"},
        },
        "final_decision": {
            "next_action": "START_QA_AGENT",
            "reviewer_identity": "claude-reviewer-1",
            "review_verdict": "NEEDS_FOLLOWUP",
            "final_pass_eligible": False,
            "verification_required_before_pass": ["focused UAT/API smoke"],
            "allowed_next_actions": ["START_QA_AGENT"],
        },
        "side_effects": [],
    }
    result.update(overrides)
    return result


def _bootstrap_profile(**overrides):
    profile = {
        "target_repo": "ollehillbom1/northstar-agent-harness-testrepo",
        "allowed_repos": ["ollehillbom1/northstar-agent-harness-testrepo"],
        "allowed_trigger_users": ["ollehillbom1"],
        "sandbox_type": "daytona",
        "disabled_integrations": ["slack", "linear"],
        "disabled_tools": ["http_request", "fetch_url", "web_search"],
        "external_setup_requested": ["github_app", "webhook", "docker_build"],
        "human_approval": None,
    }
    profile.update(overrides)
    return profile


def test_emits_file_only_human_approval_packet_for_safe_testrepo_profile():
    packet = build_bootstrap_approval_packet(_pipeline_result(), _bootstrap_profile())

    assert packet["status"] == "READY_FOR_HUMAN_APPROVAL"
    assert packet["schema_version"] == SCHEMA_VERSION
    assert packet["gate"] == "WARN"
    assert packet["TASK_ID"] == "GH-REVIEW-42-9001"
    assert packet["target_repo"] == "ollehillbom1/northstar-agent-harness-testrepo"
    assert packet["approval_packet"]["requested_external_setup"] == [
        "github_app",
        "webhook",
        "docker_build",
    ]
    assert packet["approval_packet"]["required_exact_approval"] == (
        "ALLOW_TESTREPO_BOOTSTRAP=YES repo=ollehillbom1/northstar-agent-harness-testrepo"
    )
    assert packet["approval_packet"]["dry_run_only"] is True
    assert packet["side_effects"] == []


def test_blocks_northstar_repo_until_separate_explicit_phase():
    packet = build_bootstrap_approval_packet(
        _pipeline_result(source_event={"repo": "ollehillbom1/north-star-erp"}),
        _bootstrap_profile(
            target_repo="ollehillbom1/north-star-erp",
            allowed_repos=["ollehillbom1/north-star-erp"],
        ),
    )

    assert packet["status"] == "BLOCKED"
    assert packet["gate"] == "FAIL"
    assert packet["block_reason"] == "northstar_repo_not_allowed_for_testrepo_bootstrap"
    assert packet["side_effects"] == []


def test_blocks_pipeline_with_side_effects():
    packet = build_bootstrap_approval_packet(
        _pipeline_result(side_effects=["created_github_app"]), _bootstrap_profile()
    )

    assert packet["status"] == "BLOCKED"
    assert packet["block_reason"] == "pipeline_must_be_side_effect_free"
    assert packet["gate"] == "FAIL"


def test_blocks_local_sandbox_for_autonomous_bootstrap():
    packet = build_bootstrap_approval_packet(
        _pipeline_result(), _bootstrap_profile(sandbox_type="local")
    )

    assert packet["status"] == "BLOCKED"
    assert packet["block_reason"] == "local_sandbox_not_allowed_for_autonomy"


def test_blocks_repo_not_in_allowlist():
    packet = build_bootstrap_approval_packet(
        _pipeline_result(), _bootstrap_profile(allowed_repos=["ollehillbom1/other-testrepo"])
    )

    assert packet["status"] == "BLOCKED"
    assert packet["block_reason"] == "target_repo_not_allowlisted"


def test_explicit_approval_turns_packet_into_authorized_dry_run_plan_only():
    packet = build_bootstrap_approval_packet(
        _pipeline_result(),
        _bootstrap_profile(
            human_approval="ALLOW_TESTREPO_BOOTSTRAP=YES repo=ollehillbom1/northstar-agent-harness-testrepo"
        ),
    )

    assert packet["status"] == "APPROVED_DRY_RUN_PLAN"
    assert packet["gate"] == "PASS"
    assert packet["approval_packet"]["dry_run_only"] is True
    assert packet["approval_packet"]["may_create_github_app"] is False
    assert packet["approval_packet"]["may_configure_webhook"] is False
    assert packet["approval_packet"]["may_push_or_pr"] is False
    assert packet["side_effects"] == []
