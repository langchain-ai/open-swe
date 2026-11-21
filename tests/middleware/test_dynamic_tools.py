import pytest
from langchain_core.tools import BaseTool, StructuredTool

from openswe.middleware.dynamic_tools import DynamicToolMiddleware, IntegrationGroup


def _tool(name: str) -> BaseTool:
    async def run(value: str) -> str:
        return value

    return StructuredTool.from_function(coroutine=run, name=name, description=name)


async def test_a_group_that_fails_to_build_leaves_the_others_in_the_catalog() -> None:
    async def load() -> list[BaseTool]:
        raise RuntimeError("mcp unreachable")

    middleware = DynamicToolMiddleware(
        {
            "Corridor": IntegrationGroup(tool_names=("analyzePlan",), load=load),
            "Docs": [_tool("docs-search")],
        }
    )

    assert [tool.name for tool in await middleware.catalog_tools()] == ["docs-search"]


def test_an_integration_tool_may_not_shadow_a_reserved_name() -> None:
    with pytest.raises(ValueError, match="Duplicate integration tool name"):
        DynamicToolMiddleware({"Docs": [_tool("static")]}, reserved_names={"static"})
