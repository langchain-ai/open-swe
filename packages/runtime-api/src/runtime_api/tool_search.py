"""The run's tools, which the backend keeps and reads to it.

A run's context names the tools its catalog lists, and the run reads their specs as
it starts (``TOOL_SPECS``). The rest, such as every connected integration's, stay on
the backend: the run's ``search_tools`` finds them by what they do (``SEARCH``), and
the run calls one by the name a search gave it, from code mode or directly, once the
backend has said what it is (``RESOLVE``).

A run whose catalog names its set (``Catalog.tool_set``) reads the set once per
process (``TOOL_SET``) and searches and resolves it where it runs, with the same
``ToolIndex`` the backend searches; only the grant of a tool it calls still comes
from the backend.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Final

from pydantic import BaseModel, JsonValue

from runtime_api.catalog import SEARCH_TOOLS, ToolSpec
from runtime_api.endpoint import QUICK_CALL_SECONDS, Endpoint
from runtime_api.operations import RunRequest

_RESULTS: Final = 10
_WORD: Final = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def _tokens(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text)]


def _text(tool: ToolSpec) -> list[str]:
    properties = tool.parameters.get("properties")
    parameters = " ".join(properties) if isinstance(properties, dict) else ""
    return [*_tokens(tool.description), *_tokens(parameters)]


class _Bm25:
    """Okapi BM25 over documents counted once, for every query that follows."""

    def __init__(self, documents: Sequence[Sequence[str]], k1: float = 1.2, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._counts = [Counter(document) for document in documents]
        average = sum(map(len, documents)) / len(documents) if documents else 0
        self._lengths = [len(document) / (average or 1) for document in documents]
        self._having = Counter(term for count in self._counts for term in count)

    def scores(self, query: Sequence[str]) -> list[float]:
        total = len(self._counts)
        scores = [0.0] * total
        for term in set(query):
            having = self._having[term]
            if not having:
                continue
            idf = math.log(1 + (total - having + 0.5) / (having + 0.5))
            for index, count in enumerate(self._counts):
                frequency = count[term]
                if frequency:
                    k1, b = self._k1, self._b
                    length = self._lengths[index]
                    scores[index] += (
                        idf * frequency * (k1 + 1) / (frequency + k1 * (1 - b + b * length))
                    )
        return scores


class ToolIndex:
    """A run's tools as ``search_tools`` and code mode reach them: all but those the
    graph excludes, which the model calls neither from code nor after a search.

    Built once per set and searched for every query, on the backend or in the run.
    """

    def __init__(self, tools: Sequence[ToolSpec], excluded: Iterable[str] = ()) -> None:
        kept_out = set(excluded)
        searchable = [tool for tool in tools if tool.name not in kept_out]
        self._candidates = [tool for tool in searchable if tool.name != SEARCH_TOOLS]
        # Ranked apart: in one text, a long description dilutes a match on the name, and
        # an integration's main tool has the longest description.
        self._names = _Bm25([_tokens(tool.name) for tool in self._candidates])
        self._texts = _Bm25([_text(tool) for tool in self._candidates])
        # The first tool called by each name: its own, or ``tools.<name>`` from code mode.
        self._by_name: dict[str, ToolSpec] = {}
        for tool in searchable:
            self._by_name.setdefault(tool.name, tool)
            if tool.code_name is not None:
                self._by_name.setdefault(tool.code_name, tool)

    def search(self, query: str) -> list[ToolSpec]:
        """The tools that best match ``query``: the one it names exactly, then those whose
        name matches, then those whose description or parameter names do, each by BM25."""
        terms = _tokens(query)
        names = self._names.scores(terms)
        texts = self._texts.scores(terms)
        named = self.resolve(query.strip())
        ranked = sorted(
            (
                (tool is not named, -name, -text, tool.name, tool)
                for tool, name, text in zip(self._candidates, names, texts, strict=True)
                if name > 0 or text > 0 or tool is named
            ),
            key=lambda ranking: ranking[:4],
        )
        return [ranking[-1] for ranking in ranked[:_RESULTS]]

    def resolve(self, name: str) -> ToolSpec | None:
        """The tool a run calls as ``name``: its own name, or ``tools.<name>`` from code mode."""
        return self._by_name.get(name)

    @staticmethod
    def match(tool: ToolSpec) -> ToolMatch:
        """``tool`` as a search shows it to the model."""
        return ToolMatch(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            code_name=tool.code_name,
        )


class ToolSearch(RunRequest):
    query: str


class ToolMatch(BaseModel):
    """One tool a search found, as the model reads it."""

    name: str
    description: str
    parameters: dict[str, JsonValue]
    # How code mode calls it, as ``tools.<code_name>``; None when code cannot call it.
    code_name: str | None = None


class ToolMatches(BaseModel):
    matches: list[ToolMatch] = []


class ToolLookup(RunRequest):
    # The name the run called it by: its own, or the one code mode calls it by.
    name: str


class ToolNames(RunRequest):
    names: list[str]


class ToolSpecs(BaseModel):
    # Each with the run's grant of it.
    tools: list[ToolSpec] = []


class ResolvedTool(BaseModel):
    # With the run's grant of it; None when the run has no tool by that name.
    tool: ToolSpec | None = None


class KeptTools(BaseModel):
    """Every tool the run may call, without its grants, as the backend keeps them by
    ``digest`` (``Catalog.tool_set``), and those the graph excludes."""

    digest: str
    tools: list[ToolSpec] = []
    excluded: list[str] = []


# Each only reads, so it is safe to send again.
TOOL_SPECS = Endpoint(
    "tool_specs", ToolNames, ToolSpecs, seconds=QUICK_CALL_SECONDS, delivery="idempotent"
)
SEARCH = Endpoint(
    "search_tools", ToolSearch, ToolMatches, seconds=QUICK_CALL_SECONDS, delivery="idempotent"
)
RESOLVE = Endpoint(
    "resolve_tool", ToolLookup, ResolvedTool, seconds=QUICK_CALL_SECONDS, delivery="idempotent"
)
# The run's own set, as its token names it; read once per digest by each process.
TOOL_SET = Endpoint(
    "tool_set", RunRequest, KeptTools, seconds=QUICK_CALL_SECONDS, delivery="idempotent"
)
