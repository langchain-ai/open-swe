from __future__ import annotations

import asyncio
import json
from typing import TypedDict

from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from managed_deepagents._connections import (
    AgentAuthConnectionStore,
    ConnectionHttpResponse,
    _Connections,
)


class _State(TypedDict, total=False):
    value: str


def _ok(payload: object) -> ConnectionHttpResponse:
    return {"status": 200, "body": json.dumps(payload).encode()}


class _AgentAuthResponses:
    def __init__(self) -> None:
        self._connection_lists = 0

    def __call__(self, url: str, _headers: object, _timeout: float) -> ConnectionHttpResponse:
        if "/connections/" in url:
            return _ok(
                {
                    "id": "conn-notion",
                    "slug": "notion",
                    "credential": {"kind": "oauth2"},
                }
            )
        if url.endswith("/oauth-authorization-sessions"):
            return _ok(
                {
                    "id": "sess-notion",
                    "verification_url": "https://idp.example/notion",
                }
            )
        if "/connections?" in url:
            self._connection_lists += 1
            return _ok(
                {
                    "items": [
                        {
                            "id": "conn-notion",
                            "slug": "notion",
                            "kind": "oauth2",
                            "owner_policy": "user",
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected Agent Auth URL {url!r}")


def test_custom_tool_surfaces_missing_user_grant_as_langgraph_interrupt() -> None:
    accessor = _Connections(
        AgentAuthConnectionStore(
            env={
                "LANGSMITH_API_KEY": "ls-key",
                "LANGSMITH_HOST_PROJECT_ID": "deployment-1",
                "LANGSMITH_WORKSPACE_ID": "workspace-1",
            },
            http_get=_AgentAuthResponses(),
        )
    )

    @tool
    async def read_user_connection() -> str:
        """Read the caller-owned connection."""
        return await accessor.get("notion", {"type": "user"})

    async def custom_tool(_state: _State) -> _State:
        return {"value": await read_user_connection.ainvoke({})}

    graph = (
        StateGraph(_State)
        .add_node("custom_tool", custom_tool)
        .add_edge(START, "custom_tool")
        .add_edge("custom_tool", END)
        .compile(checkpointer=InMemorySaver())
    )

    result = asyncio.run(
        graph.ainvoke(
            {},
            {
                "configurable": {
                    "thread_id": "thread-1",
                    "langgraph_auth_user": {
                        "identity": "user-1",
                        "permissions": [],
                        "mda_user_id": "user-1",
                        "mda_user_kind": "person",
                        "mda_agent_auth_principal_id": "principal-1",
                    },
                }
            },
        )
    )

    assert len(result["__interrupt__"]) == 1
    assert result["__interrupt__"][0].value == {
        "type": "credential_authorization_required",
        "message": "Connect the following integrations to continue.",
        "credentials": [
            {
                "slug": "notion",
                "kind": "oauth2",
                "connect_url": "https://idp.example/notion",
                "auth_id": "sess-notion",
            }
        ],
    }
