"""Read boundaries and report provenance for the investigation engine."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.investigations import engine, evidence_tools
from agent.investigations.models import (
    Hypothesis,
    InvestigationMessage,
    InvestigationPolicy,
    InvestigationReport,
)


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    seen: list[list[BaseMessage]] = []
    bound_names: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "investigation-test"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedModel:
        self.bound_names = [tool.name for tool in tools]
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


@pytest.fixture
def policy() -> InvestigationPolicy:
    return InvestigationPolicy(workspace_id="T1", enabled=True)


def collector(
    policy: InvestigationPolicy, before=None, *, datadog=True
) -> evidence_tools.EvidenceCollector:
    end = datetime(2026, 9, 7, tzinfo=UTC)
    return evidence_tools.EvidenceCollector(
        policy,
        window_start=end - timedelta(hours=2),
        window_end=end,
        before_tool_call=before,
        datadog_enabled=datadog,
    )


async def test_uninstalled_repo_and_malformed_service_rejected_before_upstream_access(
    policy, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Disallowed input reached a provider")

    async def no_installation(owner, name):
        return None

    monkeypatch.setattr(evidence_tools.httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(evidence_tools, "get_github_app_installation_id_for_repo", no_installation)
    collected = collector(policy)
    tools = {tool.name: tool for tool in collected.tools()}
    repo = await tools["investigate_read_repo_file"].ainvoke(
        {"repository": "elsewhere/private", "path": "app.py"}
    )
    service = await tools["investigate_datadog_logs"].ainvoke(
        {"service": "checkout-api OR service:*"}
    )
    assert "gap" in repo and "gap" in service
    assert not collected.evidence


def test_datadog_tools_follow_the_team_connection(policy):
    names = {tool.name for tool in collector(policy, datadog=False).tools()}
    assert "investigate_read_repo_file" in names
    assert not any(name.startswith("investigate_datadog") for name in names)
    assert "investigate_datadog_logs" in {tool.name for tool in collector(policy).tools()}


async def test_datadog_query_has_fixed_scope_and_reports_only_aggregates(policy, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"buckets": [{"computes": {"c0": 42}}]}})

    async def credentials():
        return evidence_tools.DatadogCredentials("datadoghq.com", "test-key", "test-app-key")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(evidence_tools, "get_datadog_credentials", credentials)
    monkeypatch.setattr(
        evidence_tools.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    checks = []

    async def before():
        checks.append("checked")

    collected = collector(policy, before)
    tools = {tool.name: tool for tool in collected.tools()}
    result = await tools["investigate_datadog_logs"].ainvoke(
        {"service": "checkout-api", "status": "error"}
    )
    assert checks == ["checked"]
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/api/v2/logs/analytics/aggregate"
    assert payload["filter"] == {
        "query": "service:checkout-api status:error",
        "from": "2026-09-06T22:00:00+00:00",
        "to": "2026-09-07T00:00:00+00:00",
    }
    assert "42" in json.dumps(result)
    assert collected.evidence[0].source == "datadog"
    assert "test-key" not in json.dumps(result)


async def test_control_revocation_stops_tool_before_request(policy, monkeypatch):
    checks = []

    async def revoked():
        checks.append(True)
        if len(checks) == 1:
            raise PermissionError("paused")

    def forbidden(*args, **kwargs):
        pytest.fail("Revoked investigation reached a provider")

    monkeypatch.setattr(evidence_tools.httpx, "AsyncClient", forbidden)
    tools = {tool.name: tool for tool in collector(policy, revoked).tools()}
    for _ in range(2):
        with pytest.raises(PermissionError, match="paused"):
            await tools["investigate_read_repo_file"].ainvoke(
                {"repository": "acme/backend", "path": "app.py"}
            )
    assert checks == [True]


async def test_model_report_drops_unknown_claims_and_uses_collected_evidence(policy, monkeypatch):
    message = InvestigationMessage(
        id="m1",
        ts="1788739200.000001",
        user="U1",
        text="Checkout errors started at 10:00.",
        source_url="https://example.slack.com/archives/C1/p1788739200000001",
    )
    fake = ScriptedModel(
        responses=[
            AIMessage(
                content=json.dumps(
                    {
                        "summary": [
                            {
                                "text": "Responders report checkout errors.",
                                "evidence_ids": ["slack:m1"],
                            }
                        ],
                        "impact": [
                            {"text": "All customers lost data.", "evidence_ids": ["invented"]}
                        ],
                        "hypotheses": [
                            {
                                "title": "A checkout failure is reported",
                                "assessment": "supported",
                                "evidence_ids": ["slack:m1"],
                            },
                            {
                                "title": "Database caused it",
                                "assessment": "supported",
                                "evidence_ids": ["invented"],
                            },
                        ],
                        "gaps": [],
                        "questions": [],
                    }
                )
            )
        ]
    )

    async def model(_policy):
        return fake

    monkeypatch.setattr(engine, "_resolve_model", model)
    report = await engine.investigate([message], policy)
    assert "Responders report checkout errors." in report.summary
    assert "slack:m1" in report.summary
    assert "lost data" not in report.impact
    assert [hyp.title for hyp in report.hypotheses] == ["A checkout failure is reported"]
    assert {item.id for item in report.evidence} == {"slack:m1"}
    assert any("citation" in gap.lower() for gap in report.gaps)
    assert not {"execute", "task", "write_file", "fetch_url"} & set(fake.bound_names)


async def test_deleted_messages_and_prior_claims_cannot_reenter_prompt(policy, monkeypatch):
    fake = ScriptedModel(responses=[AIMessage(content='{"summary": [], "impact": []}')])

    async def model(_policy):
        return fake

    monkeypatch.setattr(engine, "_resolve_model", model)
    deleted = InvestigationMessage(
        id="m1", ts="1788739200", text="deleted private text", deleted=True
    )
    report = await engine.investigate([deleted], policy)
    assert "deleted private text" not in str(fake.seen)
    assert not report.evidence
    assert report.outcome == "inconclusive"


async def test_call_budget_without_final_report_is_retryable(policy, monkeypatch):
    policy.max_model_calls = 1
    fake = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "investigate_read_repo_file",
                        "args": {"repository": "other/private", "path": "app.py"},
                        "id": "t1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    async def model(_policy):
        return fake

    monkeypatch.setattr(engine, "_resolve_model", model)
    with pytest.raises(engine.InvestigationExecutionError, match="valid evidence report"):
        await engine.investigate([], policy, question="What happened?")
    assert len(fake.seen) == 1


async def test_github_file_uses_repo_scoped_app_and_redacts_content(policy, monkeypatch):
    import base64

    token_scopes = []

    async def installation(owner, name):
        assert (owner, name) == ("acme", "backend")
        return 72

    async def token(**kwargs):
        token_scopes.append(kwargs)
        return "installation-test-token"

    def respond(request):
        assert request.method == "GET"
        assert (
            str(request.url)
            == "https://api.github.com/repos/acme/backend/contents/app.py?ref=release"
        )
        assert request.headers["authorization"] == "Bearer installation-test-token"
        content = 'password="synthetic-secret"\nreturn unavailable()'
        return httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "size": len(content),
                "content": base64.b64encode(content.encode()).decode(),
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(evidence_tools, "get_github_app_installation_id_for_repo", installation)
    monkeypatch.setattr(evidence_tools, "get_github_app_installation_token", token)
    monkeypatch.setattr(
        evidence_tools.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    collected = collector(policy)
    tools = {tool.name: tool for tool in collected.tools()}
    result = await tools["investigate_read_repo_file"].ainvoke(
        {"repository": "acme/backend", "path": "app.py", "ref": "release"}
    )
    assert token_scopes == [
        {
            "installation_id": 72,
            "repositories": ["backend"],
            "permissions": {"contents": "read"},
        }
    ]
    assert "unavailable" in result["observation"]
    assert "synthetic-secret" not in result["observation"]
    assert "synthetic-secret" not in json.dumps([item.model_dump() for item in collected.evidence])
    assert collected.evidence[0].url == "https://github.com/acme/backend/blob/release/app.py"


@pytest.mark.parametrize(
    "path", [".env", "config/.env.production", "../app.py", "/app.py", ".git/config", "deploy.key"]
)
async def test_secret_or_escaping_file_paths_never_reach_github(policy, monkeypatch, path):
    async def forbidden(*args, **kwargs):
        pytest.fail("Secret or escaping file requested a GitHub token")

    monkeypatch.setattr(evidence_tools, "get_github_app_installation_id_for_repo", forbidden)
    collected = collector(policy)
    tools = {tool.name: tool for tool in collected.tools()}
    result = await tools["investigate_read_repo_file"].ainvoke(
        {"repository": "acme/backend", "path": path}
    )
    assert "gap" in result


async def test_upstream_failure_is_a_gap_without_raw_response_or_health_claim(policy, monkeypatch):
    async def credentials():
        return evidence_tools.DatadogCredentials("datadoghq.com", "test-key", "test-app-key")

    def respond(request):
        return httpx.Response(403, json={"errors": ["customer@example.com synthetic-secret"]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(evidence_tools, "get_datadog_credentials", credentials)
    monkeypatch.setattr(
        evidence_tools.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    collected = collector(policy)
    tools = {tool.name: tool for tool in collected.tools()}
    result = await tools["investigate_datadog_logs"].ainvoke({"service": "checkout-api"})
    assert "HTTP 403" in result["gap"]
    assert "customer" not in json.dumps(result)
    assert not collected.evidence


async def test_model_tools_collect_real_evidence_before_report(policy, monkeypatch):
    async def credentials():
        return evidence_tools.DatadogCredentials("datadoghq.com", "test-key", "test-app-key")

    def respond(request):
        return httpx.Response(200, json={"data": {"buckets": [{"computes": {"c0": 42}}]}})

    class ToolReportingModel(ScriptedModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.responses:
                return await super()._agenerate(messages, stop, run_manager, **kwargs)
            self.seen.append(messages)
            observation = json.loads(messages[-1].content)
            claim = {
                "text": "There were 42 matching error logs.",
                "evidence_ids": [observation["evidence_id"]],
            }
            return ChatResult(
                generations=[
                    ChatGeneration(message=AIMessage(content=json.dumps({"summary": [claim]})))
                ]
            )

    fake = ToolReportingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "investigate_datadog_logs",
                        "args": {"service": "checkout-api"},
                        "id": "t1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    async def model(_policy):
        return fake

    original_client = httpx.AsyncClient
    monkeypatch.setattr(engine, "_resolve_model", model)
    monkeypatch.setattr(evidence_tools, "get_datadog_credentials", credentials)
    monkeypatch.setattr(
        evidence_tools.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    report = await engine.investigate([], policy, question="How many errors?")
    assert len(fake.seen) == 2
    assert report.outcome == "findings"
    assert "42 matching" in report.summary
    assert len(report.evidence) == 1
    assert report.evidence[0].source == "datadog"
    assert report.evidence[0].id in report.summary


async def test_value_error_from_control_check_propagates_out_of_engine(policy, monkeypatch):
    fake = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "investigate_datadog_logs",
                        "args": {"service": "checkout-api"},
                        "id": "t1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    async def model(_policy):
        return fake

    async def stopped():
        raise ValueError("investigation stopped")

    async def credentials():
        return evidence_tools.DatadogCredentials("datadoghq.com", "test-key", "test-app-key")

    monkeypatch.setattr(engine, "_resolve_model", model)
    monkeypatch.setattr(evidence_tools, "get_datadog_credentials", credentials)
    with pytest.raises(ValueError, match="investigation stopped"):
        await engine.investigate([], policy, before_tool_call=stopped)
    assert len(fake.seen) == 1


async def test_malformed_telemetry_does_not_become_empty_healthy_result(policy, monkeypatch):
    async def credentials():
        return evidence_tools.DatadogCredentials("datadoghq.com", "test-key", "test-app-key")

    def respond(request):
        return httpx.Response(200, json={"unexpected": "response"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(evidence_tools, "get_datadog_credentials", credentials)
    monkeypatch.setattr(
        evidence_tools.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    collected = collector(policy)
    tools = {tool.name: tool for tool in collected.tools()}
    result = await tools["investigate_datadog_logs"].ainvoke({"service": "checkout-api"})
    assert "gap" in result
    assert not collected.evidence


async def test_edited_message_invalidates_prior_claim_version(policy, monkeypatch):
    fake = ScriptedModel(responses=[AIMessage(content='{"summary": []}')])

    async def model(_policy):
        return fake

    monkeypatch.setattr(engine, "_resolve_model", model)
    old = InvestigationReport(
        summary="Old finding",
        hypotheses=[Hypothesis(title="Obsolete causal claim", evidence_ids=["slack:m1"])],
    )
    edited = InvestigationMessage(
        id="m1", ts="1788739200", edited_at="1788739300", text="The earlier report was mistaken."
    )
    report = await engine.investigate([edited], policy, previous_report=old)
    assert "Obsolete causal claim" not in str(fake.seen)
    assert report.evidence[0].id != "slack:m1"
    assert any("revalidation" in gap for gap in report.gaps)


async def test_unknown_tool_attempt_is_reported_as_coverage_gap(policy, monkeypatch):
    fake = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": "anything"},
                        "id": "t1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content='{"summary": []}'),
        ]
    )

    async def model(_policy):
        return fake

    monkeypatch.setattr(engine, "_resolve_model", model)
    report = await engine.investigate([], policy)
    assert report.outcome == "inconclusive"
    assert any("tool" in gap for gap in report.gaps)


async def test_model_factory_honors_team_gateway_and_explicit_model(policy, monkeypatch):
    calls = []

    async def team_default(role):
        assert role == "agent"
        return "openai:gpt-5.6-sol", "medium"

    async def enabled():
        return True

    def make_model(model_id, **kwargs):
        calls.append((model_id, kwargs))
        return ScriptedModel(responses=[])

    monkeypatch.setattr(engine, "get_team_default_model", team_default)
    monkeypatch.setattr(engine, "get_team_fable_enabled", enabled)
    monkeypatch.setattr(engine, "get_effective_gateway_enabled", enabled)
    monkeypatch.setattr(engine, "make_model", make_model)
    policy.model = "openai:gpt-5.6-sol"
    await engine._resolve_model(policy)
    assert calls[0][0] == "openai:gpt-5.6-sol"
    assert calls[0][1]["use_gateway"] is True
    assert calls[0][1]["max_retries"] == 0


async def test_deadline_includes_model_initialization(policy, monkeypatch):
    import asyncio

    cancelled = []

    async def model(_policy):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    original_timeout = asyncio.timeout

    def quick_timeout(seconds):
        assert seconds == 300
        return original_timeout(0.01)

    monkeypatch.setattr(engine, "_resolve_model", model)
    monkeypatch.setattr(engine.asyncio, "timeout", quick_timeout)
    with pytest.raises(engine.InvestigationExecutionError, match="time budget"):
        await engine.investigate([], policy)
    assert cancelled == [True]


async def test_span_aggregate_accepts_documented_attribute_shape(policy, monkeypatch):
    async def credentials():
        return evidence_tools.DatadogCredentials("datadoghq.com", "test-key", "test-app-key")

    def respond(request):
        assert request.url.path == "/api/v2/spans/analytics/aggregate"
        assert json.loads(request.content)["filter"]["query"] == "service:checkout-api @error:1"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "aggregate_bucket",
                        "id": "bucket",
                        "attributes": {"compute": {"c0": 12}, "by": {}},
                    }
                ]
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(evidence_tools, "get_datadog_credentials", credentials)
    monkeypatch.setattr(
        evidence_tools.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    collected = collector(policy)
    tools = {tool.name: tool for tool in collected.tools()}
    result = await tools["investigate_datadog_spans"].ainvoke({"service": "checkout-api"})
    assert "12" in result["observation"]
    assert not collected.gaps


async def test_distinct_observations_get_distinct_evidence_ids(policy, monkeypatch):
    counts = iter([4, 7])

    async def credentials():
        return evidence_tools.DatadogCredentials("datadoghq.com", "test-key", "test-app-key")

    def respond(request):
        return httpx.Response(200, json={"data": {"buckets": [{"computes": {"c0": next(counts)}}]}})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(evidence_tools, "get_datadog_credentials", credentials)
    monkeypatch.setattr(
        evidence_tools.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    collected = collector(policy)
    tools = {tool.name: tool for tool in collected.tools()}
    first = await tools["investigate_datadog_logs"].ainvoke({"service": "checkout-api"})
    second = await tools["investigate_datadog_logs"].ainvoke({"service": "checkout-api"})
    assert first["evidence_id"] != second["evidence_id"]
    assert len(collected.evidence) == 2


async def test_previous_findings_steer_the_next_pass(policy, monkeypatch):
    fake = ScriptedModel(responses=[AIMessage(content='{"summary": [], "impact": []}')])

    async def model(_policy):
        return fake

    monkeypatch.setattr(engine, "_resolve_model", model)
    previous = InvestigationReport(
        summary="Retries rose after the deploy.",
        impact="Checkout latency doubled.",
        checked=["Read 3 Slack messages."],
        questions=["Which deploy?"],
    )
    message = InvestigationMessage(id="m1", ts="1788739200", user="U1", text="deploy 42 went out")
    await engine.investigate([message], policy, previous_report=previous)
    bundle = json.loads(fake.seen[0][-1].content)
    assert bundle["previous_findings"] == {
        "summary": "Retries rose after the deploy.",
        "impact": "Checkout latency doubled.",
        "checked": ["Read 3 Slack messages."],
        "open_questions": ["Which deploy?"],
    }
