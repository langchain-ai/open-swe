"""The contribution API behind the extensible MDA primitives.

``define_extension(kind, ...)`` returns the factory a first-party primitive
calls, such as ``define_mcp(...)``. Argument parsing, branding, and manifest
stamping are shared.

This module is deliberately absent from the package's public surface. Only the
first-party factories in :mod:`managed_deepagents.namespaces` contribute
connectors, so an agent project cannot mount its own adapter or HTTP route into
the managed runtime. Opening it up to third-party packages is a later step.

The ``auth`` primitive is extended by a plain factory
rather than through here: it returns inert configuration dicts with no brand,
hooks, or normalization to share. They gain an envelope when this API is
exported and their inputs need validating at the boundary.

Every kind declares its input through ``args``: a schema that validates the
factory's keyword arguments — a pydantic model or ``TypeAdapter``, or anything
else exposing ``model_validate`` / ``validate_python`` — or a plain function
where a schema does not fit. Validating through those methods keeps the schema
library out of this package's dependencies, mirroring how the TypeScript
runtime accepts any Standard Schema vendor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    runtime_checkable,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from managed_deepagents._connector import Connector

__all__ = ["ExtensionArgs", "ExtensionKind", "define_extension"]

#: The primitives that carry a shared construction envelope.
ExtensionKind = Literal["connector"]

_ConnectorT = TypeVar("_ConnectorT", bound="Connector")


@runtime_checkable
class ModelSchema(Protocol):
    """A schema validated with ``model_validate`` — a pydantic model, say."""

    def model_validate(self, obj: Mapping[str, Any], /) -> Any: ...


@runtime_checkable
class AdapterSchema(Protocol):
    """A schema validated with ``validate_python`` — a pydantic ``TypeAdapter``."""

    def validate_python(self, obj: Mapping[str, Any], /) -> Any: ...


#: How author input becomes the arguments a primitive is built from.
ExtensionArgs: TypeAlias = "ModelSchema | AdapterSchema | Callable[..., Any]"


def define_extension(
    kind: Literal["connector"],
    *,
    label: str,
    connector: type[_ConnectorT],
    args: ExtensionArgs,
) -> Callable[..., _ConnectorT]:
    """Build a validated connector factory."""
    if kind == "connector":
        return _connector_extension(label=label, connector=connector, args=args)
    raise TypeError(f"Unknown extension kind: {kind!r}")


def _connector_extension(
    *,
    label: str,
    connector: type[_ConnectorT],
    args: ExtensionArgs,
) -> Callable[..., _ConnectorT]:
    """A connector contributes tools, HTTP routes, sandbox, events, or messaging.

    The brand comes from subclassing :class:`~managed_deepagents._connector.Connector`,
    so the envelope is the validated construction step.
    """
    parse = _parser(label, args)

    def factory(**kwargs: object) -> _ConnectorT:
        return connector(**_as_mapping(label, parse(kwargs)))

    return factory


def _parser(label: str, args: ExtensionArgs) -> Callable[[Mapping[str, object]], Any]:
    """Resolve ``args`` into the function the factory runs on its keyword arguments.

    A schema validates the keyword arguments as a mapping, so the same model
    works for a factory and for anything else that already holds the config as
    data. Schema checks come first because a pydantic model class is callable
    too, and calling it would bypass ``model_validate``.
    """
    if isinstance(args, ModelSchema):
        validate = args.model_validate
    elif isinstance(args, AdapterSchema):
        validate = args.validate_python
    elif callable(args):
        return lambda kwargs: args(**kwargs)
    else:
        raise TypeError(f"{label}(...) `args` must be a schema or a callable.")

    def parse(kwargs: Mapping[str, object]) -> Any:
        try:
            return validate(dict(kwargs))
        except ValueError as error:
            raise ValueError(f"{label}(...) received invalid configuration:\n{error}") from error

    return parse


def _as_mapping(label: str, parsed: Any) -> Mapping[str, Any]:
    """Read parsed args as keyword arguments, dumping a validated model if needed.

    Options left unset are dropped rather than carried as ``None``, so a model
    produces the same payload a Zod schema does in the TypeScript runtime —
    which matters because those options are forwarded to provider clients that
    treat a missing key and an explicit ``None`` differently. Aliases win over
    field names, because an alias is there to spell a key the way an author
    writes it (``from``, say, which cannot be a Python identifier).
    """
    if isinstance(parsed, Mapping):
        return parsed
    dump = getattr(parsed, "model_dump", None)
    if callable(dump):
        return cast("Mapping[str, Any]", dump(exclude_none=True, by_alias=True))
    raise TypeError(
        f"{label}(...) `args` must produce a mapping or a model with `model_dump()`; "
        f"got {type(parsed).__name__}."
    )
