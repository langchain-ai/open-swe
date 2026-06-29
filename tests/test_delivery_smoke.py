from __future__ import annotations

from typing import Any

import pytest

from agent import delivery_queue as queue
from agent import delivery_review as review
from agent import delivery_runner as runner
from agent import delivery_smoke as smoke
from agent import project_registry, project_secrets
from agent.dashboard import provider_pat_vault
from agent.merge_controller import MergeResult


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def search_items(
        self,
        namespace: list[str],
        filter: dict[str, Any] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        values = [
            value
            for (stored_namespace, _), value in self.items.items()
            if stored_namespace == tuple(namespace)
        ]
        if filter:
            values = [
                value
                for value in values
                if all(value.get(key) == expected for key, expected in filter.items())
            ]
        return {"items": [{"value": value} for value in values[offset : offset + limit]]}


class _FakeThreads:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, Any]] = {}

    async def create(
        self,
        *,
        thread_id: str,
        metadata: dict[str, Any],
        if_exists: str = "raise",
    ) -> dict[str, Any]:
        self.metadata.setdefault(thread_id, dict(metadata))
        return {"thread_id": thread_id, "metadata": self.metadata[thread_id]}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.metadata.setdefault(thread_id, {}).update(metadata)

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {
            "thread_id": thread_id,
            "status": "idle",
            "metadata": self.metadata.get(thread_id, {}),
        }


class _FakeRuns:
    async def list(
        self,
        thread_id: str,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return []


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.threads = _FakeThreads()
        self.runs = _FakeRuns()


class _FakeLinearClient:
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        self.list_kwargs: dict[str, Any] | None = None

    async def list_issues(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_kwargs = kwargs
        return self.issues


class _DispatchRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        thread_id: str,
        content: str,
        configurable: dict[str, Any],
        *,
        source: str,
        assistant_id: str = "agent",
        metadata: dict[str, Any] | None = None,
        client: Any = None,
    ) -> dict[str, Any]:
        run_id = f"run-{assistant_id}-{len(self.calls) + 1}"
        self.calls.append(
            {
                "thread_id": thread_id,
                "content": content,
                "configurable": configurable,
                "source": source,
                "assistant_id": assistant_id,
                "metadata": metadata,
                "client": client,
                "run_id": run_id,
            }
        )
        return {"run_id": run_id}


class _MergeRecorder:
    def __init__(self, result: MergeResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> MergeResult:
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    from cryptography.fernet import Fernet

    client = _FakeClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
    monkeypatch.setattr(project_registry, "_client", lambda: client)
    monkeypatch.setattr(project_secrets, "_client", lambda: client)
    monkeypatch.setattr(provider_pat_vault, "_client", lambda: client)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return client


@pytest.fixture
def dispatch_recorder(monkeypatch: pytest.MonkeyPatch) -> _DispatchRecorder:
    recorder = _DispatchRecorder()
    monkeypatch.setattr(runner, "dispatch_agent_run", recorder)
    monkeypatch.setattr(review, "dispatch_agent_run", recorder)
    return recorder


def _issue(**overrides: Any) -> dict[str, Any]:
    issue = {
        "id": "lin-sports-1",
        "identifier": "SPORT-123",
        "title": "Fix Sports CMS teaser card",
        "description": "The teaser card component loses its CTA state on mobile.",
        "url": "https://linear.app/example/issue/SPORT-123",
        "priority": 2,
        "priorityLabel": "High",
        "state": {"id": "state-ready", "name": "Ready", "type": "started"},
        "team": {"id": "team-sports", "key": "SPORT", "name": "Sports CMS"},
        "project": {"id": "linear-sports", "name": "Sports CMS"},
        "labels": {"nodes": [{"id": "label-ready", "name": "agent-ready"}]},
    }
    return {**issue, **overrides}


def _project_overrides() -> dict[str, Any]:
    return {
        "credential_policy": {
            "provider": "github",
            "scope": "user",
            "requires_user_pat": True,
            "identity": "github:user:octocat",
        },
        "merge_policy": {
            "enabled": True,
            "strategy": "squash",
            "target_branch": "main",
            "required_checks": ["tests"],
        },
    }


def _worker_result(**overrides: Any) -> dict[str, Any]:
    gates = [
        "drupal_bootstrap",
        "theme_assets",
        "sdc_twig_render",
        "browser_flow",
        "screenshot",
        "trace_or_video",
        "pr_qa_evidence",
    ]
    result = {
        "cause": "The teaser SDC variant missed the mobile CTA modifier.",
        "changed_files": ["web/themes/custom/sports/components/teaser-card/teaser-card.twig"],
        "before_proof": "Mobile CTA state missing in fixture render.",
        "after_proof": "Mobile CTA state renders in Drupal preview.",
        "executed_gates": [
            {"name": gate, "status": "passed", "source": "platform"} for gate in gates
        ],
        "risks": [],
        "pull_request_summary": "Fix teaser card mobile CTA state.",
        "branch_created": True,
        "draft_pull_request_created": True,
        "sandbox_evidence": {
            "provider": "drupal",
            "server_side": True,
            "preview_url": "https://preview.example.test/sports/teaser",
        },
        "preview_url": "https://preview.example.test/sports/teaser",
        "screenshots": ["https://artifacts.example.test/sports/teaser.png"],
        "traces": ["https://artifacts.example.test/sports/trace.zip"],
        "required_checks": [{"name": "tests", "status": "completed", "conclusion": "success"}],
        "pr": {
            "number": 42,
            "url": "https://github.com/example/sports-cms/pull/42",
            "state": "open",
            "draft": False,
            "head": {"sha": "head-sha"},
            "base": {"ref": "main"},
        },
    }
    result.update(overrides)
    return result


async def test_sports_cms_smoke_drives_linear_item_to_auto_merge(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_sports-cms-token-1234",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_BASE_URL",
        value="https://ai-hub.example/v1",
        updated_by="octocat",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_API_KEY",
        value="valid-key",
        updated_by="octocat",
    )
    linear_client = _FakeLinearClient([_issue()])
    merge_recorder = _MergeRecorder(MergeResult(True, "merged", "merged", sha="merge-sha"))

    proof = await smoke.run_sports_cms_delivery_smoke(
        tracker_config={"team_keys": ["SPORT"], "project_ids": ["linear-sports"]},
        vcs_config={"owner": "example", "repo": "sports-cms"},
        project_overrides=_project_overrides(),
        linear_client=linear_client,
        client=fake_client,
        external_work_item_id="lin-sports-1",
        worker_result=_worker_result(),
        review_result={
            "reviewed_sha": "head-sha",
            "qa_result": {"passed": True, "artifacts": ["qa-report.json"]},
            "findings": [],
        },
        merge_token="token",
        merge_func=merge_recorder,
    )

    final_item = await queue.read_delivery_queue_item("sports-cms:linear:lin-sports-1")
    assert proof["status"] == "passed"
    assert proof["acceptance"] == {
        "linear_issue_queued": True,
        "worker_branch_created": True,
        "draft_pull_request_created": True,
        "drupal_sandbox_preview": True,
        "qa_evidence_complete": True,
        "independent_review_and_qa": True,
        "agent_reviewed_auto_merge": True,
    }
    assert final_item["status"] == "done"
    assert final_item["smoke_proof"]["status"] == "passed"
    assert final_item["smoke_proof"]["acceptance"] == proof["acceptance"]
    assert final_item["merge_status"] == "merged"
    assert final_item["merge_commit_sha"] == "merge-sha"
    assert final_item["branch"] == "delivery/sports-cms/lin-sports-1"
    assert final_item["repo"] == {"owner": "example", "name": "sports-cms"}
    assert final_item["required_checks"] == [
        {"name": "tests", "status": "completed", "conclusion": "success"}
    ]
    assert [call["assistant_id"] for call in dispatch_recorder.calls] == [
        "agent",
        "reviewer",
        "agent",
    ]
    assert merge_recorder.calls[0]["owner"] == "example"
    assert merge_recorder.calls[0]["repo"] == "sports-cms"
    assert merge_recorder.calls[0]["pr_number"] == 42


async def test_sports_cms_smoke_uses_configured_ddev_runtime_profile(
    fake_client: _FakeClient,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    project_path = tmp_path / "sports-cms"
    project_path.mkdir()
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setenv("SPORTS_CMS_DDEV_PROJECT_PATH", str(project_path))
    monkeypatch.setenv("SPORTS_CMS_PREVIEW_URL", "https://sports-preview.test/")
    monkeypatch.setenv("SPORTS_CMS_THEME_PATH", "web/themes/custom/sports_theme")
    monkeypatch.setenv("SPORTS_CMS_ARTIFACT_DIR", str(artifact_dir))

    proof = await smoke.run_sports_cms_delivery_smoke(
        tracker_config={"team_keys": ["SPORT"], "project_ids": ["linear-sports"]},
        vcs_config={"owner": "example", "repo": "sports-cms"},
        linear_client=_FakeLinearClient([]),
        client=fake_client,
        worker_result=_worker_result(),
        review_result={"qa_result": {"passed": True}},
        merge_token="token",
    )

    stored_project = await project_registry.get_delivery_project("sports-cms")
    assert proof["status"] == "blocked"
    assert proof["reason"] == "linear_issue_not_queued"
    assert stored_project is not None
    assert stored_project["sandbox_profile"]["provider"] == "ddev"
    runtime = stored_project["sandbox_profile"]["runtime"]
    assert runtime["project_path"] == str(project_path)
    assert runtime["preview_url"] == "https://sports-preview.test/"
    assert runtime["gates"][1]["command"] == "test -f web/themes/custom/sports_theme/build/main.min.css"
    assert runtime["gates"][4]["artifact_path"] == str(artifact_dir / "sports-cms-home.png")


async def test_sports_cms_smoke_blocks_before_merge_without_draft_pr_proof(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_sports-cms-token-1234",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_BASE_URL",
        value="https://ai-hub.example/v1",
        updated_by="octocat",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_API_KEY",
        value="valid-key",
        updated_by="octocat",
    )
    linear_client = _FakeLinearClient([_issue()])
    merge_recorder = _MergeRecorder(MergeResult(True, "merged", "merged", sha="merge-sha"))

    proof = await smoke.run_sports_cms_delivery_smoke(
        tracker_config={"team_keys": ["SPORT"], "project_ids": ["linear-sports"]},
        vcs_config={"owner": "example", "repo": "sports-cms"},
        project_overrides=_project_overrides(),
        linear_client=linear_client,
        client=fake_client,
        external_work_item_id="lin-sports-1",
        worker_result=_worker_result(draft_pull_request_created=False),
        review_result={
            "reviewed_sha": "head-sha",
            "qa_result": {"passed": True},
            "findings": [],
        },
        merge_token="token",
        merge_func=merge_recorder,
    )

    final_item = await queue.read_delivery_queue_item("sports-cms:linear:lin-sports-1")
    assert proof["status"] == "blocked"
    assert proof["reason"] == "draft_pull_request_missing"
    assert proof["acceptance"]["draft_pull_request_created"] is False
    assert final_item["status"] == "blocked"
    assert final_item["blocker_reason"] == "draft_pull_request_missing"
    assert merge_recorder.calls == []
    assert [call["assistant_id"] for call in dispatch_recorder.calls] == [
        "agent",
        "reviewer",
        "agent",
    ]
