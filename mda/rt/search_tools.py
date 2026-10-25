"""``search_tools``: the run's operations that best match what the model looks for.

Code mode keeps most tools off the model's list, so the model finds them here, with
the schemas it needs to call them and the name code mode calls them by. The run's
catalog holds every operation, so the search runs here, over it.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Final

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import JsonValue
from runtime_api.catalog import SEARCH_TOOLS, OperationSpec

_MAX_RESULTS: Final = 20
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


def search_tools_tool(
    operations: Sequence[OperationSpec], description: str | None = None
) -> BaseTool:
    """``search_tools`` over ``operations``, ranked by BM25 over name, description and parameters."""
    ranked = sorted(operations, key=lambda operation: operation.name)
    documents = [_operation_tokens(operation) for operation in ranked]

    async def search_tools(query: str, limit: int = 5) -> list[dict[str, JsonValue]]:
        scores = bm25(_tokens(query), documents)
        best = sorted(
            (pair for pair in zip(ranked, scores, strict=True) if pair[1] > 0),
            key=lambda pair: -pair[1],
        )
        return [
            {
                "name": operation.name,
                "description": operation.description,
                "parameters": operation.parameters,
                "code_name": operation.code_name,
            }
            for operation, _ in best[: max(1, min(limit, _MAX_RESULTS))]
            if operation.name != SEARCH_TOOLS
        ]

    return StructuredTool.from_function(
        coroutine=search_tools,
        name=SEARCH_TOOLS,
        # The backend's description, which says how to call what it finds.
        description=description or "Search the tools available in this run.",
    )
