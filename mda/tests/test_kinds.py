import pytest
from rt.backend import Run, RunContext
from rt.kinds import Graph
from runtime_api.catalog import Catalog, LoopRules, SandboxCreate, SandboxSpec
from runtime_api.connections import ConnectionRef


@pytest.fixture(autouse=True)
def _backend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")


def _run(graph: Graph, catalog: Catalog) -> Run:
    context = {"graph": graph, "__run_token": "token", "__catalog": catalog.model_dump(mode="json")}
    return Run(RunContext.model_validate(context))


@pytest.mark.parametrize("graph", ["reviewer", "review-scout"])
def test_a_review_follows_its_kinds_rules_whatever_its_catalog_says(graph: Graph) -> None:
    catalog = Catalog(loop=LoopRules(deployment_prompt="Staging."), model_call_limit=7)

    planned = _run(graph, catalog).planned

    assert {"glob", "grep"} <= set(planned.loop.hidden_tools)
    assert planned.loop.at_end
    assert planned.loop.deployment_prompt == "Staging."
    assert planned.model_call_limit != 7
    assert planned.files.guards == []


def test_the_agent_keeps_its_catalogs_loop_and_guards_its_shell() -> None:
    rules = LoopRules(task_coordination=True, refresh_github_proxy=True)

    planned = _run("agent", Catalog(loop=rules, model_call_limit=7)).planned

    assert planned.loop == rules
    assert planned.model_call_limit == 7
    assert planned.files.guards == ["shell_commands"]


def test_mda_keeps_a_proxy_it_writes_fresh_itself() -> None:
    sandbox = SandboxSpec(
        thread_id="thread-1",
        create=SandboxCreate(fs_capacity_bytes=1, idle_ttl_seconds=1, delete_after_stop_seconds=1),
        github_proxy=ConnectionRef(slug="github", owner="agent"),
    )

    planned = _run("reviewer", Catalog(sandbox=sandbox)).planned

    assert not planned.loop.refresh_github_proxy
