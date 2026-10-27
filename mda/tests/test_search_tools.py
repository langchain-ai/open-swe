from langchain_core.tools import StructuredTool
from rt.search_tools import SearchableTools, search_tools_tool
from runtime_api.catalog import OperationSpec


def _operation(name: str, description: str, code_name: str | None) -> OperationSpec:
    return OperationSpec(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        code_name=code_name,
    )


async def test_search_tools_ranks_by_bm25_and_names_code_calls() -> None:
    searchable = SearchableTools(
        [
            _operation("searchDatadogLogs", "Search raw Datadog log entries.", "searchDatadogLogs"),
            _operation("get_datadog_metric", "Query metrics from Datadog.", "getDatadogMetric"),
            _operation("save_issue", "Create or update a Linear issue.", "saveIssue"),
            _operation("slack_reply", "Reply in Slack.", None),
        ]
    )
    search = search_tools_tool(searchable)

    results = await search.ainvoke({"query": "datadog logs", "limit": 2})

    assert [result["name"] for result in results] == ["searchDatadogLogs", "get_datadog_metric"]
    assert results[0]["parameters"]["properties"].keys() == {"query"}
    assert results[0]["code_name"] == "searchDatadogLogs"
    assert await search.ainvoke({"query": "kubernetes"}) == []


async def test_search_tools_finds_the_mcp_tools_mda_loads_by_their_code_names() -> None:
    searchable = SearchableTools([_operation("save_issue", "Create a Linear issue.", "saveIssue")])
    search = search_tools_tool(searchable)

    async def spans(query: str) -> str:
        return query

    searchable.add(
        [
            StructuredTool.from_function(
                coroutine=spans,
                name="mcp_datadog_search_spans",
                description="Search Datadog APM spans.",
            )
        ]
    )

    [result] = await search.ainvoke({"query": "datadog spans", "limit": 1})
    assert result["name"] == "mcp_datadog_search_spans"
    assert result["code_name"] == "mcpDatadogSearchSpans"
    assert result["parameters"]["properties"].keys() == {"query"}
