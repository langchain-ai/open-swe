"""Synthetic activity for the temporary cutover rehearsal; never a deployment entrypoint."""

import os
from typing import TypedDict

from fastapi import FastAPI
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph_sdk import get_client


class State(TypedDict, total=False):
    action: str
    invocation_id: str
    pr_number: int
    opened_at: str
    counter: int
    completion_recorded: bool
    preference: str


async def activity(state: State) -> dict:
    client = get_client(url=os.environ["LANGGRAPH_URL"])
    stored = await client.store.get_item(["user_preferences"], "cutover-reader")
    result = {
        "counter": state.get("counter", 0) + 1,
        "preference": stored["value"]["theme"] if stored else "missing",
    }
    if state.get("action") == "operational":
        return result

    from agent.analytics.usage import (
        record_agent_invocation_completion,
        record_agent_invocation_cost,
        record_agent_invocation_usage,
        record_agent_pr_usage,
    )
    from agent.utils.run_usage import RunUsageSummary

    thread_id = get_config()["configurable"]["thread_id"]
    invocation_id = state["invocation_id"]
    if state["action"] == "new":
        await record_agent_invocation_usage(
            invocation_id=invocation_id,
            thread_id=thread_id,
            github_login="cutover-reader",
            github_user_id=123,
            user_email=None,
            model_id="synthetic-model",
            effort=None,
            source="dashboard",
        )
    result["completion_recorded"] = await record_agent_invocation_completion(
        invocation_id=invocation_id,
        thread_id=thread_id,
        usage=RunUsageSummary(models=("synthetic-model",), total_tokens=30),
    )
    await record_agent_invocation_cost(invocation_id=invocation_id, cost_usd=0.25)
    if state.get("pr_number"):
        await record_agent_pr_usage(
            invocation_id=invocation_id,
            thread_id=thread_id,
            github_login="cutover-reader",
            user_email=None,
            owner="cutover-org",
            repo="fixture",
            pr_number=state["pr_number"],
            pr_url=None,
            head="fixture",
            base="main",
            model_id="synthetic-model",
            repository_private=False,
            created_at=state.get("opened_at"),
            additions=4,
        )
    return result


builder = StateGraph(State)
builder.add_node("activity", activity)
builder.add_edge(START, "activity")
builder.add_edge("activity", END)
graph = builder.compile()

if os.environ.get("CUTOVER_PHASE") == "legacy":
    app = FastAPI()
else:
    from agent.analytics.worker import stop_worker
    from agent.api.app import create_app

    app = create_app()

    @app.post("/cutover-fixture/pause-worker")
    async def pause_worker() -> dict:
        await stop_worker()
        return {"paused": True}
