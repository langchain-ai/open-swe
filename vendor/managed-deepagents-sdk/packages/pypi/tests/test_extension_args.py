"""How ``define_extension`` turns author input into a primitive's arguments.

Mirrors ``packages/npm/src/extension.test.ts``: a schema from whichever library
the contributor already uses, or a plain function where a schema does not fit.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field, TypeAdapter

from managed_deepagents._connector import Connector
from managed_deepagents._extension import define_extension


class _Bitbucket(BaseModel):
    workspace: str = Field(min_length=1)
    repositories: list[str] = []


class _BitbucketConnector(Connector):
    kind = "bitbucket"

    def __init__(self, *, workspace: str, repositories: list[str]) -> None:
        self.workspace = workspace
        self.repositories = repositories


def _factory(args: Any) -> Any:
    return define_extension(
        "connector",
        label="connectors.bitbucket",
        connector=_BitbucketConnector,
        args=args,
    )


def test_builds_a_primitive_from_a_pydantic_model_defaults_included() -> None:
    connector = _factory(_Bitbucket)(workspace="acme")

    assert connector.workspace == "acme"
    assert connector.repositories == []


def test_reports_the_failing_field_under_the_factory_own_name() -> None:
    with pytest.raises(ValueError, match=r"connectors\.bitbucket\(\.\.\.\) received invalid"):
        _factory(_Bitbucket)(workspace="")


def test_accepts_a_type_adapter_as_well_as_a_model() -> None:
    adapter = TypeAdapter(_Bitbucket)
    connector = _factory(adapter)(workspace="acme", repositories=["acme/api"])

    assert connector.repositories == ["acme/api"]


def test_accepts_any_schema_exposing_model_validate() -> None:
    class Handwritten:
        """No pydantic — the envelope only needs the validation method."""

        def model_validate(self, obj: dict[str, Any]) -> dict[str, Any]:
            workspace = obj.get("workspace")
            if not isinstance(workspace, str) or not workspace:
                raise ValueError("workspace must be a non-empty string")
            return {"workspace": workspace.upper(), "repositories": []}

    connector = _factory(Handwritten())(workspace="acme")

    assert connector.workspace == "ACME"
    with pytest.raises(ValueError, match="workspace must be a non-empty string"):
        _factory(Handwritten())(workspace="")


def test_still_takes_a_plain_function_for_what_a_schema_cannot_express() -> None:
    def parse(*, workspace: str, repositories: list[str] | None = None) -> dict[str, Any]:
        return {"workspace": workspace, "repositories": repositories or []}

    connector = _factory(parse)(workspace="acme")

    assert connector.workspace == "acme"


def test_rejects_args_that_is_neither_a_schema_nor_a_callable() -> None:
    with pytest.raises(TypeError, match="must be a schema or a callable"):
        _factory("not a schema")
