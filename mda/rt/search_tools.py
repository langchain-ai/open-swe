"""``search_tools``: the run's operations that best match what the model looks for.

Code mode keeps most tools off the model's list, so the model finds them here, with
the schemas it needs to call them and the name code mode calls them by. The run's
catalog holds every operation, and MDA loads the MCP tools the run calls itself, so
the search runs here, over both.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Final

from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

# The name code mode exposes each tool under, which nothing public names.
from langchain_quickjs._ptc import to_camel_case  # noqa: PLC2701
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import SEARCH_TOOLS, OperationSpec

_MAX_RESULTS: Final = 20
_json_object = TypeAdapter(dict[str, JsonValue])
_WORD: Final = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def _tokens(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text)]


def _operation_tokens(operation: OperationSpec) -> list[str]:
    properties = operation.parameters.get("properties")
    parameters = " ".join(properties) if isinstance(properties, dict) else ""
    # A match on the name says more than one buried in a long description.
    return [
        *_tokens(operation.name) * 3,
        *_tokens(operation.description),
        *_tokens(parameters),
    ]


def bm25(
    query: Sequence[str], documents: Sequence[Sequence[str]], k1: float = 1.2, b: float = 0.75
) -> list[float]:
    """Okapi BM25 score of each document for ``query``."""
    if not documents:
        return []
    average = sum(map(len, documents)) / len(documents) or 1
    counts = [Counter(document) for document in documents]
    scores = [0.0] * len(documents)
    for term in set(query):
        having = sum(1 for count in counts if term in count)
        if not having:
            continue
        idf = math.log(1 + (len(documents) - having + 0.5) / (having + 0.5))
        for index, count in enumerate(counts):
            frequency = count[term]
            if frequency:
                length = len(documents[index]) / average
                scores[index] += (
                    idf * frequency * (k1 + 1) / (frequency + k1 * (1 - b + b * length))
                )
    return scores


class SearchableTools:
    """What ``search_tools`` searches: the catalog's operations, and the MCP tools MDA
    loads for the run itself, added as a model call first offers them."""

    def __init__(self, operations: Sequence[OperationSpec]) -> None:
        self._operations = sorted(operations, key=lambda operation: operation.name)
        self._documents = [_operation_tokens(operation) for operation in self._operations]
        self._names = {operation.name for operation in self._operations}

    def add(self, tools: Iterable[BaseTool]) -> None:
        for tool in tools:
            if tool.name in self._names:
                continue
            operation = OperationSpec(
                name=tool.name,
                description=tool.description,
                parameters=_json_object.validate_python(
                    convert_to_openai_tool(tool)["function"]["parameters"]
                ),
                code_name=to_camel_case(tool.name),
            )
            self._names.add(tool.name)
            self._operations.append(operation)
            self._documents.append(_operation_tokens(operation))

    def ranked(self, query: str) -> list[OperationSpec]:
        scores = bm25(_tokens(query), self._documents)
        return [
            operation
            for operation, _ in sorted(
                (pair for pair in zip(self._operations, scores, strict=True) if pair[1] > 0),
                key=lambda pair: -pair[1],
            )
        ]


def search_tools_tool(searchable: SearchableTools, description: str | None = None) -> BaseTool:
    """``search_tools`` over ``searchable``, ranked by BM25 over name, description and parameters."""

    async def search_tools(query: str, limit: int = 5) -> list[dict[str, JsonValue]]:
        return [
            {
                "name": operation.name,
                "description": operation.description,
                "parameters": operation.parameters,
                "code_name": operation.code_name,
            }
            for operation in searchable.ranked(query)[: max(1, min(limit, _MAX_RESULTS))]
            if operation.name != SEARCH_TOOLS
        ]

    return StructuredTool.from_function(
        coroutine=search_tools,
        name=SEARCH_TOOLS,
        # The backend's description, which says how to call what it finds.
        description=description or "Search the tools available in this run.",
    )
