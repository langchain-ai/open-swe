from __future__ import annotations

import json
from pathlib import Path
import types

import local_fix_agent as lfa


PROXY_SCRIPT = """
import argparse
import logging
import os
import urllib.request

def build_proxy_map() -> dict[str, str]:
    proxies = {}
    for key, scheme in [("HTTP_PROXY", "http"), ("HTTPS_PROXY", "https")]:
        value = os.getenv(key) or os.getenv(key.lower())
        if value:
            proxies[scheme] = value
    return proxies

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(build_proxy_map()))
    print(opener)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
""".strip() + "\n"

LOCAL_SCRIPT = """
def slugify(value: str) -> str:
    lowered = value.strip().lower().replace("_", "-")
    return "-".join(part for part in lowered.split("-") if part)
""".strip() + "\n"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def build_learning_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    proxy_path = repo / "examples" / "proxy_client.py"
    local_path = repo / "examples" / "local_slugify.py"
    write(proxy_path, PROXY_SCRIPT)
    write(local_path, LOCAL_SCRIPT)
    return repo, proxy_path, local_path


def test_learn_from_scripts_creates_pattern_memory(tmp_path: Path) -> None:
    repo, proxy_path, local_path = build_learning_repo(tmp_path)

    result = lfa.learn_from_scripts(repo, [str(proxy_path), str(local_path)])
    memory = lfa.load_script_pattern_memory(repo)

    assert result["learned_sources"] == ["examples/local_slugify.py", "examples/proxy_client.py"]
    assert len(memory["patterns"]) >= 4
    assert any(pattern["pattern_type"] == "proxy_handling" for pattern in memory["patterns"])
    assert any(pattern["pattern_type"] == "validation_strategy" for pattern in memory["patterns"])


def test_retrieve_script_patterns_prefers_proxy_patterns_for_proxy_task(tmp_path: Path) -> None:
    repo, proxy_path, local_path = build_learning_repo(tmp_path)
    lfa.learn_from_scripts(repo, [str(proxy_path), str(local_path)])
    memory = lfa.load_script_pattern_memory(repo)

    selection = lfa.retrieve_script_patterns(memory, "debug", "debug proxy timeout failures in network cli")

    applied = [item["pattern_type"] for item in selection["applied"]]
    assert "proxy_handling" in applied
    assert "timeout" in applied or "request_session" in applied


def test_retrieve_script_patterns_avoids_irrelevant_network_patterns_for_local_task(tmp_path: Path) -> None:
    repo, proxy_path, local_path = build_learning_repo(tmp_path)
    lfa.learn_from_scripts(repo, [str(proxy_path), str(local_path)])
    memory = lfa.load_script_pattern_memory(repo)

    selection = lfa.retrieve_script_patterns(memory, "validation_discovery", "local utility no network only local helper validation")

    applied = {item["pattern_type"] for item in selection["applied"]}
    assert "proxy_handling" not in applied
    assert "retry_backoff" not in applied
    assert selection["rejected"]


def test_render_new_script_uses_learned_conventions(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    lfa.learn_from_scripts(repo, [str(proxy_path)])
    memory = lfa.load_script_pattern_memory(repo)
    output_path = repo / "generated_tool.py"

    selection = lfa.retrieve_script_patterns(memory, "new-script", "create a local cli tool with logging")
    rendered = lfa.render_new_script(repo, output_path, "normalize names", selection)

    assert rendered["path"] == str(output_path)
    content = output_path.read_text()
    assert "argparse.ArgumentParser" in content
    assert "logging.basicConfig" in content
    assert 'if __name__ == "__main__":' in content


def test_run_pattern_learning_eval_compares_baseline_and_learned(tmp_path: Path) -> None:
    repo, proxy_path, local_path = build_learning_repo(tmp_path)
    eval_root = repo / "evals" / "pattern_learning"
    write(eval_root / "examples" / "proxy_client.py", PROXY_SCRIPT)
    write(eval_root / "examples" / "local_slugify.py", LOCAL_SCRIPT)
    (eval_root / "examples").mkdir(parents=True, exist_ok=True)
    tasks = {
        "tasks": [
            {
                "id": "proxy-debug",
                "task_type": "debug",
                "script": "examples/proxy_client.py",
                "prompt": "debug proxy-aware cli",
                "expected_pattern_types": ["cli_style", "proxy_handling", "validation_strategy"],
                "forbidden_pattern_types": ["retry_backoff"],
                "expected_conventions": ["argparse", "main_guard", "logging", "proxy"],
            },
            {
                "id": "new-local-script",
                "task_type": "new-script",
                "prompt": "create local cli utility",
                "output_name": "generated_local_tool.py",
                "expected_pattern_types": ["cli_style", "entrypoint", "logging_style"],
                "forbidden_pattern_types": ["proxy_handling", "request_session", "retry_backoff"],
                "expected_conventions": ["argparse", "main_guard", "logging"],
            },
        ]
    }
    write(eval_root / "tasks.json", json.dumps(tasks, indent=2) + "\n")
    lfa.learn_from_scripts(repo, [str(proxy_path), str(local_path)])

    result = lfa.run_pattern_learning_eval(repo, str(eval_root / "tasks.json"))

    assert result["task_count"] == 2
    assert result["summary"]["learned_average_score"] >= result["summary"]["baseline_average_score"]
    assert any(run["selection"]["considered"] for run in result["learned_runs"])


def test_correctness_requires_expected_validation_kind(tmp_path: Path) -> None:
    repo, proxy_path, local_path = build_learning_repo(tmp_path)
    eval_root = repo / "evals" / "pattern_learning"
    write(eval_root / "examples" / "local_slugify.py", LOCAL_SCRIPT)
    task = {
        "id": "local-validation",
        "task_type": "validation_discovery",
        "script": "examples/local_slugify.py",
        "prompt": "Find a safe validation command for a simple local helper utility with no network behavior.",
        "expected_pattern_types": ["function_organization", "validation_strategy"],
        "required_validation_kind": "function",
        "correctness_command": "python -c \"import pathlib, sys; sys.path.insert(0, str(pathlib.Path('.').resolve())); import examples.local_slugify as mod; assert mod.slugify(' A_B ')=='a-b'; print('slugify-ok')\"",
        "correctness_expected_contains": ["slugify-ok"],
    }
    lfa.learn_from_scripts(repo, [str(proxy_path), str(local_path)])
    memory = lfa.load_script_pattern_memory(repo)

    baseline = lfa.run_pattern_eval_mode(repo, task, memory, use_learned_patterns=False)
    learned = lfa.run_pattern_eval_mode(repo, task, memory, use_learned_patterns=True)

    assert baseline["correctness_pass"] is False
    assert learned["correctness_pass"] is True
    assert learned["validation_kind"] == "function"


def test_retrieve_script_patterns_groups_duplicate_families(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    lfa.learn_from_scripts(repo, [str(proxy_path), str(proxy_path)])
    memory = lfa.load_script_pattern_memory(repo)

    selection = lfa.retrieve_script_patterns(memory, "debug", "proxy timeout network cli")

    considered_types = [item["pattern_type"] for item in selection["considered"]]
    assert considered_types.count("cli_style") <= 1


def test_validated_script_is_promoted_and_relearned(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"

    result = lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["network", "proxy"], note="seed")
    memory = lfa.load_script_pattern_memory(pattern_repo)
    catalog = lfa.load_pattern_source_catalog(pattern_repo)

    assert result["imported_sources"]
    imported_rel = result["imported_sources"][0]["repo_rel_path"]
    candidate_rel = result["imported_sources"][0]["candidate_path"]
    assert result["imported_sources"][0]["promotion_state"] == "curated"
    assert result["imported_sources"][0]["validation_status"] == "passed"
    assert result["imported_sources"][0]["promoted"] is True
    assert result["relearn_triggered"] is True
    assert (pattern_repo / candidate_rel).exists()
    assert (pattern_repo / imported_rel).exists()
    assert catalog["sources"][0]["origin_path"] == str(proxy_path)
    assert any(pattern["source_repo_path"] == imported_rel for pattern in memory["patterns"])
    assert any(pattern["trust_level"] == "trusted" for pattern in memory["patterns"])
    assert result["imported_sources"][0]["source_type"] == "local"
    assert result["imported_sources"][0]["acquisition_method"] == "direct"
    assert result["imported_sources"][0]["proxy_used"] is False


def test_relearn_patterns_loads_from_pattern_repo_catalog(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    imported = lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted")

    memory_path = lfa.pattern_repo_storage_path(pattern_repo, lfa.SCRIPT_PATTERN_MEMORY_FILE_NAME)
    memory_path.unlink()
    relearned = lfa.relearn_patterns_from_repo(pattern_repo)

    assert relearned["learned_patterns"]


def test_inspect_patterns_returns_expected_structure(tmp_path: Path) -> None:
    repo, proxy_path, local_path = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy", "network"])
    lfa.import_pattern_files(pattern_repo, [str(local_path)], trust_level="experimental", tags=["local"])

    inspection = lfa.inspect_patterns(pattern_repo)

    assert inspection["summary"]["total_patterns"] >= 1
    assert inspection["summary"]["curated_trusted"] >= 1
    assert inspection["summary"]["candidate"] >= 1
    first = inspection["patterns"][0]
    assert {
        "id",
        "pattern_type",
        "source_file",
        "source_origin",
        "trust_level",
        "promotion_state",
        "tags",
        "applicability_context",
        "confidence",
        "validation_result",
        "publish_result",
        "regression_status",
        "last_validated_commit",
        "last_published_commit",
        "pr_url",
        "promotion_reason",
        "timestamp",
    }.issubset(first.keys())


def test_inspect_patterns_filter_state_works(tmp_path: Path) -> None:
    repo, proxy_path, local_path = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    lfa.import_pattern_files(pattern_repo, [str(local_path)], trust_level="experimental", tags=["local"])

    trusted_only = lfa.inspect_patterns(pattern_repo, filter_state="curated_trusted")

    assert trusted_only["patterns"]
    assert all(item["promotion_state"] == "curated_trusted" for item in trusted_only["patterns"])


def test_inspect_patterns_filter_tag_works(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy", "network"])

    filtered = lfa.inspect_patterns(pattern_repo, filter_tag="proxy")

    assert filtered["patterns"]
    assert all("proxy" in item["tags"] for item in filtered["patterns"])


def test_inspect_patterns_search_and_limit_work(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy", "retry"])

    filtered = lfa.inspect_patterns(pattern_repo, search="retry", limit=1)

    assert len(filtered["patterns"]) == 1
    assert "retry" in json.dumps(filtered["patterns"][0], sort_keys=True).lower()


def test_inspect_patterns_none_repo_returns_empty_result() -> None:
    inspection = lfa.inspect_patterns(None)

    assert inspection["pattern_repo"] == "none"
    assert inspection["summary"]["total_patterns"] == 0
    assert inspection["patterns"] == []


def test_inspect_pattern_sources_respects_repo_selection(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    default_repo = tmp_path / "default_repo"
    proxy_repo = tmp_path / "proxy_repo"
    lfa.import_pattern_files(default_repo, [str(proxy_path)], trust_level="experimental", tags=["generic"])
    lfa.import_pattern_files(proxy_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy", "network"])
    config = {
        "pattern_repo": str(default_repo),
        "pattern_repos": {"proxy": {"path": str(proxy_repo), "tags": ["proxy", "network"]}},
    }

    selection = lfa.select_pattern_repo(config, "proxy", "debug", "debug proxy timeout failures in network cli", script_path=proxy_path)
    inspection = lfa.inspect_pattern_sources(selection["path"])

    assert inspection["pattern_repo"] == str(proxy_repo.resolve())
    assert inspection["summary"]["curated_trusted"] >= 1
    assert any(source["trust_level"] == "trusted" for source in inspection["sources"])


def test_promote_pattern_records_manual_metadata(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    pattern_id = lfa.inspect_patterns(pattern_repo)["patterns"][0]["id"]

    result = lfa.manage_pattern_state(pattern_repo, pattern_id, action="demote")
    inspection = lfa.inspect_patterns(pattern_repo, search=pattern_id)

    assert result["ok"] is True
    assert result["previous_state"] == "curated_trusted"
    assert result["new_state"] == "curated_experimental"
    assert inspection["patterns"][0]["promotion_method"] == "manual"
    assert "manual demote pattern override" in inspection["patterns"][0]["promotion_reason"]


def test_demote_pattern_to_candidate_removes_from_effective_memory(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="experimental", tags=["proxy"])
    pattern_id = lfa.inspect_patterns(pattern_repo)["patterns"][0]["id"]

    first = lfa.manage_pattern_state(pattern_repo, pattern_id, action="demote")
    second = lfa.manage_pattern_state(pattern_repo, pattern_id, action="demote")
    inspection = lfa.inspect_patterns(pattern_repo, search=pattern_id)

    assert first["new_state"] == "candidate"
    assert second["new_state"] == "candidate"
    assert inspection["patterns"] == []


def test_forget_pattern_hides_it_from_inspection(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    pattern_id = lfa.inspect_patterns(pattern_repo)["patterns"][0]["id"]

    result = lfa.manage_pattern_state(pattern_repo, pattern_id, action="forget")
    inspection = lfa.inspect_patterns(pattern_repo, search=pattern_id)

    assert result["ok"] is True
    assert result["new_state"] == "forgotten"
    assert inspection["patterns"] == []


def test_promote_source_makes_candidate_patterns_effective(tmp_path: Path) -> None:
    repo, _, local_path = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    imported = lfa.import_pattern_files(pattern_repo, [str(local_path)], trust_level="experimental", tags=["local"])
    source_id = imported["imported_sources"][0]["id"]

    result = lfa.manage_source_state(pattern_repo, source_id, action="promote")
    inspection = lfa.inspect_patterns(pattern_repo, filter_state="curated_experimental")

    assert result["ok"] is True
    assert result["previous_state"] == "candidate"
    assert result["new_state"] == "curated_experimental"
    assert inspection["patterns"]


def test_demote_source_reduces_trust_in_listing(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    imported = lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    source_id = imported["imported_sources"][0]["id"]

    result = lfa.manage_source_state(pattern_repo, source_id, action="demote")
    inspection = lfa.inspect_patterns(pattern_repo, filter_state="curated_experimental")

    assert result["ok"] is True
    assert result["new_state"] == "curated_experimental"
    assert inspection["patterns"]
    assert all(item["trust_level"] == "experimental" for item in inspection["patterns"])


def test_forget_source_removes_it_from_active_registry(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    imported = lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    source_id = imported["imported_sources"][0]["id"]

    result = lfa.manage_source_state(pattern_repo, source_id, action="forget")
    inspection = lfa.inspect_pattern_sources(pattern_repo)

    assert result["ok"] is True
    assert result["new_state"] == "forgotten"
    assert all(source["path"] != imported["imported_sources"][0]["repo_rel_path"] for source in inspection["sources"])


def test_pattern_inspection_json_includes_manual_metadata(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    pattern_id = lfa.inspect_patterns(pattern_repo)["patterns"][0]["id"]
    lfa.manage_pattern_state(pattern_repo, pattern_id, action="demote")

    payload = lfa.inspect_patterns(pattern_repo, search=pattern_id)
    encoded = json.dumps(payload, sort_keys=True)

    assert "promotion_method" in encoded
    assert "promotion_reason" in encoded


def test_pattern_control_dry_run_does_not_mutate(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    before = lfa.inspect_patterns(pattern_repo)
    pattern_id = before["patterns"][0]["id"]

    result = lfa.manage_pattern_state(pattern_repo, pattern_id, action="demote", dry_run=True)
    after = lfa.inspect_patterns(pattern_repo)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert before == after


def test_trusted_patterns_influence_more_than_experimental(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    experimental = lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="experimental")
    assert experimental["imported_sources"][0]["promotion_state"] == "curated"
    lfa.relearn_patterns_from_repo(pattern_repo)
    memory = lfa.load_script_pattern_memory(pattern_repo)

    selection = lfa.retrieve_script_patterns(memory, "debug", "debug proxy timeout failures in network cli")

    assert "proxy_handling" not in [item["pattern_type"] for item in selection["applied"]]
    assert any(item["pattern_type"] == "proxy_handling" for item in selection["rejected"])

    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted")
    memory = lfa.load_script_pattern_memory(pattern_repo)
    selection = lfa.retrieve_script_patterns(memory, "debug", "debug proxy timeout failures in network cli")
    assert "proxy_handling" in [item["pattern_type"] for item in selection["applied"]]


def test_list_pattern_sources_reports_trust_and_origin(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])

    sources = lfa.list_pattern_sources(pattern_repo)

    assert len(sources) == 1
    assert sources[0]["trust_level"] == "trusted"
    assert sources[0]["origin_path"] == str(proxy_path)


def test_default_training_repo_created_when_missing(tmp_path: Path, monkeypatch) -> None:
    repo_path = tmp_path / "default_pattern_repo"
    monkeypatch.setattr(lfa, "default_pattern_repo_path", lambda: repo_path)

    resolved, created = lfa.ensure_pattern_repo_status(lfa.default_pattern_repo_path())

    assert resolved == repo_path.resolve()
    assert created is True
    assert resolved.exists()
    assert (resolved / "candidates").exists()
    assert (resolved / "curated" / "trusted").exists()
    assert (resolved / "curated" / "experimental").exists()


def test_existing_training_repo_reused_without_reset(tmp_path: Path, monkeypatch) -> None:
    repo_path = tmp_path / "default_pattern_repo"
    repo_path.mkdir(parents=True)
    (repo_path / "sentinel.txt").write_text("keep\n")
    monkeypatch.setattr(lfa, "default_pattern_repo_path", lambda: repo_path)

    resolved, created = lfa.ensure_pattern_repo_status(lfa.default_pattern_repo_path())

    assert created is False
    assert (resolved / "sentinel.txt").read_text() == "keep\n"


def test_reset_pattern_repo_only_when_explicit(tmp_path: Path) -> None:
    pattern_repo = tmp_path / "pattern_repo"
    pattern_repo.mkdir(parents=True)
    (pattern_repo / "sentinel.txt").write_text("remove\n")

    reset_repo, existed = lfa.reset_pattern_repo(pattern_repo)

    assert existed is True
    assert reset_repo.exists()
    assert not (reset_repo / "sentinel.txt").exists()


def test_sanitization_replaces_sensitive_values_before_storage(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "secret_client.py"
    write(
        source,
        """
API_KEY = "sk-live-abcdef1234567890"
PASSWORD = "super-secret"
HTTP_PROXY = "http://user:pass@proxy.internal:8080"
AUTHORIZATION = "Bearer token-value"
COOKIE = "sessionid=abc123"
""".strip()
        + "\n",
    )
    pattern_repo = tmp_path / "pattern_repo"

    result = lfa.import_pattern_files(pattern_repo, [str(source)], trust_level="trusted")

    stored = pattern_repo / result["imported_sources"][0]["candidate_path"]
    content = stored.read_text()
    assert "sk-live" not in content
    assert "super-secret" not in content
    assert "user:pass@" not in content
    assert "token-value" not in content
    assert "sessionid=abc123" not in content
    assert "<REDACTED_SECRET>" in content or "<REDACTED_TOKEN>" in content
    assert result["imported_sources"][0]["sanitized_changed"] is True


def test_parse_pattern_import_source_supports_ssh_and_http() -> None:
    ssh_legacy = lfa.parse_pattern_import_source("alice@example.com:/srv/tool.py")
    ssh_url = lfa.parse_pattern_import_source("ssh://alice@example.com/srv/tool.py")
    http_url = lfa.parse_pattern_import_source("https://example.com/tools/tool.py")

    assert ssh_legacy["source_type"] == "ssh"
    assert ssh_legacy["ssh_host"] == "alice@example.com"
    assert ssh_legacy["ssh_path"] == "/srv/tool.py"
    assert ssh_url["source_type"] == "ssh"
    assert ssh_url["ssh_host"] == "alice@example.com"
    assert ssh_url["ssh_path"] == "/srv/tool.py"
    assert http_url["source_type"] == "http"
    assert http_url["display_name"] == "tool.py"


def test_fetch_pattern_source_http_uses_proxy_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(lfa, "command_available", lambda name: name == "curl")
    monkeypatch.setattr(lfa, "CURRENT_SUBPROCESS_ENV", {"HTTP_PROXY": "http://proxy:8080"})
    monkeypatch.setattr(lfa, "run_subprocess", lambda command, cwd, shell=False: (0, "print('ok')\n"))

    fetched = lfa.fetch_pattern_source("https://example.com/tool.py", Path("/tmp/repo"))

    assert fetched["ok"] is True
    assert fetched["source_type"] == "http"
    assert fetched["acquisition_method"] == "curl"
    assert fetched["proxy_used"] is True


def test_configure_subprocess_safety_propagates_all_proxy() -> None:
    lfa.configure_subprocess_safety(
        {
            "HTTP_PROXY": "http://proxy:8080",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "socks5://proxy:1080",
            "run_budget": 0,
            "attempt_budget": 0,
        }
    )

    assert lfa.CURRENT_SUBPROCESS_ENV["HTTP_PROXY"] == "http://proxy:8080"
    assert lfa.CURRENT_SUBPROCESS_ENV["ALL_PROXY"] == "socks5://proxy:1080"


def test_fetch_pattern_source_missing_curl_blocks_http_import(monkeypatch) -> None:
    monkeypatch.setattr(lfa, "command_available", lambda name: False)

    fetched = lfa.fetch_pattern_source("https://example.com/tool.py", Path("/tmp/repo"))

    assert fetched["ok"] is False
    assert fetched["blocked_reason"] == "missing required tool: curl"


def test_fetch_pattern_source_missing_ssh_and_scp_blocks_ssh_import(monkeypatch) -> None:
    monkeypatch.setattr(lfa, "command_available", lambda name: False)

    fetched = lfa.fetch_pattern_source("alice@example.com:/srv/tool.py", Path("/tmp/repo"))

    assert fetched["ok"] is False
    assert fetched["blocked_reason"] == "missing required tool: ssh (and scp unavailable)"


def test_import_pattern_files_remote_http_source_records_metadata(tmp_path: Path, monkeypatch) -> None:
    pattern_repo = tmp_path / "pattern_repo"
    monkeypatch.setattr(
        lfa,
        "fetch_pattern_source",
        lambda source, cwd: {
            "ok": True,
            "source_type": "http",
            "source_origin": source,
            "acquisition_method": "curl",
            "proxy_used": True,
            "content": PROXY_SCRIPT,
            "display_name": "proxy_client.py",
        },
    )

    result = lfa.import_pattern_files(pattern_repo, ["https://example.com/proxy_client.py"], trust_level="trusted")
    source = result["imported_sources"][0]

    assert source["source_type"] == "http"
    assert source["source_origin"] == "https://example.com/proxy_client.py"
    assert source["acquisition_method"] == "curl"
    assert source["proxy_used"] is True
    assert source["validation_passed"] is True
    assert source["promoted"] is True


def test_import_pattern_files_remote_candidate_not_promoted_when_validation_blocks(tmp_path: Path, monkeypatch) -> None:
    pattern_repo = tmp_path / "pattern_repo"
    monkeypatch.setattr(
        lfa,
        "fetch_pattern_source",
        lambda source, cwd: {
            "ok": True,
            "source_type": "ssh",
            "source_origin": source,
            "acquisition_method": "scp",
            "proxy_used": False,
            "content": "def broken(:\n",
            "display_name": "broken.py",
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_candidate_validation",
        lambda current_repo, candidate_path: {
            "plan": {"limited_validation": False, "only_syntax_import_validation": False, "primary_command": "python -m py_compile broken.py"},
            "result": {"ok": False, "output": "syntax error"},
            "passed": False,
            "limited_validation": False,
            "validation_command": "python -m py_compile broken.py",
        },
    )
    monkeypatch.setattr(lfa, "repair_training_candidate", lambda current_repo, candidate_path: {"ok": False, "output": "blocked", "command": []})

    result = lfa.import_pattern_files(pattern_repo, ["alice@example.com:/srv/broken.py"], trust_level="trusted")
    source = result["imported_sources"][0]

    assert source["source_type"] == "ssh"
    assert source["acquisition_method"] == "scp"
    assert source["validation_passed"] is False
    assert source["promoted"] is False
    assert source["promotion_state"] == "candidate"


def test_import_pattern_files_acquisition_failure_records_blocked_source(tmp_path: Path, monkeypatch) -> None:
    pattern_repo = tmp_path / "pattern_repo"
    monkeypatch.setattr(
        lfa,
        "fetch_pattern_source",
        lambda source, cwd: {
            "ok": False,
            "source_type": "http",
            "source_origin": source,
            "acquisition_method": "curl",
            "proxy_used": True,
            "blocked_reason": "missing required tool: curl",
            "display_name": "tool.py",
        },
    )

    result = lfa.import_pattern_files(pattern_repo, ["https://example.com/tool.py"], trust_level="trusted")
    source = result["imported_sources"][0]

    assert source["candidate_imported"] is False
    assert source["validation_status"] == "blocked"
    assert source["blocked_reason"] == "missing required tool: curl"


def test_candidate_import_does_not_immediately_become_trusted_when_validation_is_weak(tmp_path: Path) -> None:
    repo, _, local_path = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"

    result = lfa.import_pattern_files(pattern_repo, [str(local_path)], trust_level="trusted")
    source = result["imported_sources"][0]
    memory = lfa.load_script_pattern_memory(pattern_repo)

    assert source["promotion_state"] == "candidate"
    assert source["promoted"] is False
    assert source["validation_status"] == "passed"
    assert source["limited_validation"] is True
    assert result["relearn_triggered"] is False
    assert memory["patterns"] == []


def test_invalid_script_is_repaired_before_promotion(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "broken_cli.py"
    write(source, "def broken(:\n")
    pattern_repo = tmp_path / "pattern_repo"
    validation_calls = {"count": 0}

    def fake_run_candidate_validation(current_repo: Path, candidate_path: Path) -> dict:
        validation_calls["count"] += 1
        if validation_calls["count"] == 1:
            return {
                "plan": {"limited_validation": False, "only_syntax_import_validation": False, "primary_command": "python -m py_compile broken_cli.py"},
                "result": {"ok": False, "output": "syntax error"},
                "passed": False,
                "limited_validation": False,
                "validation_command": "python -m py_compile broken_cli.py",
            }
        candidate_path.write_text(PROXY_SCRIPT)
        return {
            "plan": {"limited_validation": False, "only_syntax_import_validation": False, "primary_command": "python broken_cli.py --help"},
            "result": {"ok": True, "output": ""},
            "passed": True,
            "limited_validation": False,
            "validation_command": "python broken_cli.py --help",
        }

    monkeypatch.setattr(lfa, "run_candidate_validation", fake_run_candidate_validation)
    monkeypatch.setattr(lfa, "repair_training_candidate", lambda current_repo, candidate_path: {"ok": True, "output": "repaired", "command": []})

    result = lfa.import_pattern_files(pattern_repo, [str(source)], trust_level="trusted")
    source_record = result["imported_sources"][0]

    assert source_record["repair_needed"] is True
    assert source_record["promoted"] is True
    assert source_record["promotion_state"] == "curated"
    assert result["relearn_triggered"] is True


def test_blocked_script_is_not_promoted_to_trusted(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "blocked_cli.py"
    write(source, "def broken(:\n")
    pattern_repo = tmp_path / "pattern_repo"

    monkeypatch.setattr(
        lfa,
        "run_candidate_validation",
        lambda current_repo, candidate_path: {
            "plan": {"limited_validation": False, "only_syntax_import_validation": False, "primary_command": "python -m py_compile blocked_cli.py"},
            "result": {"ok": False, "output": "syntax error"},
            "passed": False,
            "limited_validation": False,
            "validation_command": "python -m py_compile blocked_cli.py",
        },
    )
    monkeypatch.setattr(lfa, "repair_training_candidate", lambda current_repo, candidate_path: {"ok": False, "output": "blocked", "command": []})

    result = lfa.import_pattern_files(pattern_repo, [str(source)], trust_level="trusted")
    source_record = result["imported_sources"][0]

    assert source_record["promoted"] is False
    assert source_record["promotion_state"] == "candidate"
    assert source_record["validation_status"] == "blocked"
    assert result["relearn_triggered"] is False


def test_script_without_add_to_training_does_not_import(tmp_path: Path, monkeypatch) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    imported_calls: list[list[str]] = []
    monkeypatch.setattr(lfa, "import_pattern_files", lambda *args, **kwargs: imported_calls.append(list(args[1])) or {})
    monkeypatch.setattr(lfa, "run_fix_loop", lambda *args, **kwargs: None, raising=False)

    args = types.SimpleNamespace(
        repo=str(repo),
        script=str(proxy_path),
        target="",
        test_cmd="",
        test_cmd_positional=[],
        mode="quick",
        last=False,
        continue_run=False,
        from_last_failure=False,
        reuse_last_test=False,
        dry_run=True,
        explain_only=True,
        show_diff=False,
        publish=False,
        publish_on_success=False,
        no_publish_on_success=False,
        publish_only=False,
        publish_branch="",
        publish_pr=False,
        publish_merge=False,
        publish_merge_local_main=False,
        publish_message="",
        pattern_repo=str(pattern_repo),
        reset_pattern_repo=False,
        import_pattern_files=None,
        add_to_training=False,
        pattern_trust="trusted",
        pattern_tags="",
        pattern_note="",
        list_patterns=False,
        list_pattern_sources=False,
        relearn_patterns=False,
        forget_pattern="",
        learn_from=None,
        new_script="",
        new_script_purpose="",
        eval_pattern_learning=False,
        pattern_eval_tasks="",
        http_proxy="",
        https_proxy="",
        api_budget_run=None,
        api_budget_attempt=None,
        config="",
        max_steps=None,
        max_file_chars=None,
    )

    repo_resolved, *_ = lfa.resolve_run_settings(args, require_test_cmd=False)

    assert repo_resolved == proxy_path.parent.resolve()
    assert imported_calls == []


def test_script_with_add_to_training_imports(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"

    result = lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted")

    assert result["imported_sources"]
    assert result["learned_pattern_delta"] > 0
    assert result["imported_sources"][0]["promotion_state"] == "curated"
    assert result["relearn_triggered"] is True


def test_candidate_sources_do_not_influence_normal_runs_until_curated(tmp_path: Path) -> None:
    repo, _, local_path = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"

    result = lfa.import_pattern_files(pattern_repo, [str(local_path)], trust_level="trusted")
    memory = lfa.load_script_pattern_memory(pattern_repo)
    selection = lfa.retrieve_script_patterns(memory, "validation_discovery", "local helper validation")

    assert result["imported_sources"][0]["promotion_state"] == "candidate"
    assert result["relearn_triggered"] is False
    assert memory["patterns"] == []
    assert selection["applied"] == []


def test_task_selects_domain_specific_pattern_repo(tmp_path: Path) -> None:
    repo, proxy_path, local_path = build_learning_repo(tmp_path)
    default_repo = tmp_path / "default_repo"
    proxy_repo = tmp_path / "proxy_repo"
    lfa.import_pattern_files(default_repo, [str(local_path)], trust_level="trusted", tags=["local"])
    lfa.import_pattern_files(proxy_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy", "network"])
    config = {
        "pattern_repo": str(default_repo),
        "pattern_repos": {
            "proxy": {"path": str(proxy_repo), "tags": ["proxy", "network"]},
        },
    }

    selection = lfa.select_pattern_repo(config, "auto", "debug", "debug proxy timeout failures in network cli", script_path=proxy_path)

    assert selection["selected"] == "proxy"
    assert selection["path"] == proxy_repo.resolve()
    assert selection["confidence"] in {"medium", "high"}


def test_task_falls_back_to_default_pattern_repo(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    default_repo = tmp_path / "default_repo"
    proxy_repo = tmp_path / "proxy_repo"
    lfa.import_pattern_files(default_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy", "network"])
    config = {
        "pattern_repo": str(default_repo),
        "pattern_repos": {
            "local_utils": {"path": str(proxy_repo), "tags": ["local", "utility"]},
        },
    }

    selection = lfa.select_pattern_repo(config, "auto", "debug", "debug proxy timeout failures in network cli", script_path=proxy_path)

    assert selection["selected"] == "default"
    assert selection["path"] == default_repo.resolve()


def test_task_selects_none_when_no_relevant_repo_exists(tmp_path: Path) -> None:
    repo, _, local_path = build_learning_repo(tmp_path)
    default_repo = tmp_path / "default_repo"
    lfa.import_pattern_files(default_repo, [str(local_path)], trust_level="trusted", tags=["local", "utility"])
    config = {"pattern_repo": str(default_repo)}

    selection = lfa.select_pattern_repo(config, "auto", "debug", "streaming websocket backpressure frame parser")

    assert selection["selected"] == "none"
    assert selection["path"] is None


def test_pattern_repo_override_forces_specific_repo(tmp_path: Path) -> None:
    repo, proxy_path, local_path = build_learning_repo(tmp_path)
    default_repo = tmp_path / "default_repo"
    proxy_repo = tmp_path / "proxy_repo"
    lfa.import_pattern_files(default_repo, [str(local_path)], trust_level="trusted")
    lfa.import_pattern_files(proxy_repo, [str(proxy_path)], trust_level="trusted")
    config = {
        "pattern_repo": str(default_repo),
        "pattern_repos": {
            "proxy": str(proxy_repo),
        },
    }

    selection = lfa.select_pattern_repo(config, "proxy", "validation_discovery", "local helper validation")

    assert selection["selected"] == "proxy"
    assert selection["path"] == proxy_repo.resolve()
    assert "operator override" in selection["reason"]


def test_pattern_repo_override_disables_repo_usage(tmp_path: Path) -> None:
    repo, _, local_path = build_learning_repo(tmp_path)
    default_repo = tmp_path / "default_repo"
    lfa.import_pattern_files(default_repo, [str(local_path)], trust_level="trusted")
    config = {"pattern_repo": str(default_repo)}

    selection = lfa.select_pattern_repo(config, "none", "validation_discovery", "local helper validation", script_path=local_path)

    assert selection["selected"] == "none"
    assert selection["path"] is None
    assert selection["confidence"] == "high"


def test_cli_option_value_supports_split_and_equals_forms() -> None:
    assert lfa.cli_option_value(["--config", "/tmp/config.json"], "--config") == "/tmp/config.json"
    assert lfa.cli_option_value(["--pattern-repo=auto"], "--pattern-repo") == "auto"
    assert lfa.cli_option_value(["--other", "value"], "--config") is None


def test_selected_proxy_repo_with_only_generic_patterns_has_no_domain_coverage(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    proxy_repo = tmp_path / "proxy_repo"
    lfa.import_pattern_files(proxy_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy", "network"])
    selection, repo_selection = lfa.resolve_pattern_selection(
        {"pattern_repo": str(tmp_path / "empty_default_repo")},
        {"selected": "proxy", "path": proxy_repo, "reason": "test", "confidence": "medium", "tags": ["proxy", "network"]},
        "new-script",
        "create local cli tool with logging",
        script_path=tmp_path / "tool.py",
    )

    assert repo_selection["selected"] == "none"
    assert selection["coverage"]["domain_coverage_ok"] is True
    assert selection["fallback_reason"] == "domain repo lacked domain-specific coverage; fell back to no pattern repo"


def test_selected_proxy_repo_with_real_domain_pattern_has_domain_coverage(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    proxy_repo = tmp_path / "proxy_repo"
    lfa.import_pattern_files(proxy_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy", "network"])
    selection, repo_selection = lfa.resolve_pattern_selection(
        {},
        {"selected": "proxy", "path": proxy_repo, "reason": "test", "confidence": "medium", "tags": ["proxy", "network"]},
        "debug",
        "debug proxy timeout failures in network cli",
        script_path=proxy_path,
    )

    assert repo_selection["selected"] == "proxy"
    assert selection["coverage"]["domain_coverage_ok"] is True
    assert "proxy_handling" in selection["coverage"]["domain_specific_patterns_applied"]


def test_low_domain_coverage_lowers_repo_confidence() -> None:
    coverage = {
        "is_domain_repo": True,
        "domain_coverage_ok": False,
    }
    refined = lfa.refine_repo_confidence("high", coverage, {"selected": "proxy"}, {"repos": {}}, "new-script")
    assert refined == "medium"


def test_effectiveness_history_influences_future_repo_ranking(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    default_repo = tmp_path / "default_repo"
    proxy_repo = tmp_path / "proxy_repo"
    lfa.import_pattern_files(default_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    lfa.import_pattern_files(proxy_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    lfa.save_pattern_effectiveness(
        proxy_repo,
        {
            "families": {},
            "repos": {
                "proxy": {
                    "times_applied": 4,
                    "times_successful": 4,
                    "times_overapplied": 0,
                    "task_types": {"debug": {"times_applied": 4, "times_successful": 4, "times_overapplied": 0}},
                }
            },
            "task_types": {},
            "updated_at": 1,
        },
    )
    config = {
        "pattern_repo": str(default_repo),
        "pattern_repos": {
            "proxy": {"path": str(proxy_repo), "tags": ["proxy"]},
        },
    }

    selection = lfa.select_pattern_repo(config, "auto", "debug", "debug proxy timeout failures in network cli", script_path=proxy_path)

    assert selection["selected"] == "proxy"


def test_overapplied_pattern_families_are_downranked(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    memory = lfa.load_script_pattern_memory(pattern_repo)
    effectiveness = {
        "families": {
            "proxy_handling": {
                "times_applied": 4,
                "times_successful": 0,
                "times_overapplied": 4,
                "task_types": {"debug": {"times_applied": 4, "times_successful": 0, "times_overapplied": 4}},
            }
        },
        "repos": {},
        "task_types": {},
    }

    selection = lfa.retrieve_script_patterns(
        memory,
        "debug",
        "debug proxy timeout failures in network cli",
        script_path=proxy_path,
        effectiveness=effectiveness,
    )

    assert "proxy_handling" not in [item["pattern_type"] for item in selection["applied"]]


def test_compare_pattern_baseline_reports_difference(tmp_path: Path) -> None:
    repo, proxy_path, _ = build_learning_repo(tmp_path)
    pattern_repo = tmp_path / "pattern_repo"
    lfa.import_pattern_files(pattern_repo, [str(proxy_path)], trust_level="trusted", tags=["proxy"])
    selection, _ = lfa.resolve_pattern_selection(
        {},
        {"selected": "proxy", "path": pattern_repo, "reason": "test", "confidence": "medium", "tags": ["proxy"]},
        "new-script",
        "proxy-aware network cli utility",
        script_path=tmp_path / "out.py",
    )
    plan = {"primary_command": "python out.py --help", "chosen_stack": [{"kind": "syntax", "command": "python -m py_compile out.py"}]}

    comparison = lfa.compare_pattern_baseline(plan, selection)

    assert "baseline_validation_command" in comparison
    assert "learned_validation_command" in comparison
    assert isinstance(comparison["patterns_added"], list)
