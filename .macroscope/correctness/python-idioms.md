---
# Python idiom instructions folded into Macroscope's built-in Correctness review.
#
# Docs checked: 2026-07-28.
# - Custom correctness instructions (mechanism + frontmatter):
#   https://docs.macroscope.com/custom-instructions
#
# This is a CORRECTNESS INSTRUCTION, not a Check Run Agent. Per the docs, files
# under .macroscope/correctness/ are folded into the same built-in Correctness
# review for every changed file whose path matches the include/exclude globs
# below; multiple matching files stack. There is NO separate check run, NO
# `conclusion`, and NO blocking posture of its own — findings surface as ordinary
# Correctness comments (Source = "Correctness" in the Code Review activity log).
#
# Frontmatter for correctness instructions supports ONLY `include`/`exclude`
# (both optional globs). Check Run Agent fields (title, model, reasoning, effort,
# input, tools, conclusion, showToolCalls, waitsFor) are meaningless here and are
# deliberately omitted.
#
# SCOPE — org layer only. This repo is a fork of langchain-ai/open-swe; upstream
# Python does not follow Speed Bay house style and must not be flagged against
# it. These idioms apply only to Speed Bay-owned files (FORK.md § Speed Bay org
# layer and § File placement rule). Upstream-file deviations (marked SPEEDBAY
# DEVIATION) stay minimal by policy and follow upstream's local style, so they
# are deliberately out of scope here too.
include:
  - "speedbay/**/*.py"
  - "agent/middleware/speedbay_*.py"
  - "agent/utils/speedbay_*.py"
  - "agent/integrations/docker_local.py"
---

# Python correctness idioms (Speed Bay org layer)

These idioms are the Speed Bay house style, mirrored from the warehouse
monorepo's Python contract and inlined here so this file is self-contained.
Encode only the **semantic** idioms a linter cannot mechanically enforce.
`ruff`/`mypy` and CI already own formatting, unused imports, and basic typing —
do not restate them here, and do not add generic "look for bugs" language that
duplicates the built-in Correctness check. Flag a changed line only when it
violates one of the idioms below; stay quiet on intentional patterns.

## Domain models and boundaries

- Domain models must be immutable by default and use `attrs.frozen`. Flag a new
  mutable domain model (plain class with reassigned attributes, `@dataclass`
  without `frozen=True`, or `attrs` without `frozen`) unless mutability is the
  domain behavior being modeled and that invariant is documented at the boundary
  that exposes it.
- Flag persistence, transport, or framework concerns leaking into domain models
  — database row shapes, ORM base classes, or raw API payloads defining the
  domain type. Adapt at the edge instead of letting the wire shape become the
  domain shape.
- Flag missing invariant protection at construction time where a model has a real
  invariant: prefer validators, converters, or named construction functions over
  trusting raw inputs.

## Imports and module hygiene

- Flag abstract container and callable types imported from `typing`
  (`typing.Mapping`, `typing.Sequence`, `typing.Callable`, `typing.Iterable`,
  etc.) instead of `collections.abc`.
- Flag wildcard imports (`from module import *`).
- Flag import-time side effects (I/O, network, mutation of global state,
  registration) in a module that is not an explicit application entrypoint.

## Testing

- Flag any new `unittest` usage in tests: `unittest.TestCase` subclasses,
  `unittest.mock`-first structure, or assertion methods such as
  `self.assertEqual`. New tests use `pytest` with plain `assert`, fixtures,
  parametrization, and pytest-native exception checks.
- Flag tests that assert through private functions, storage internals, or
  internal call ordering rather than the public interface / observable behavior.

## Protocols and interfaces

- Flag structural seams expressed as concrete-implementation dependencies where a
  small, purpose-specific `Protocol` naming the behavior a caller requires would
  decouple them. A `Protocol` should name the required behavior, not mirror every
  method on an implementation.
- Flag boolean-flag APIs and broad options bags that fold distinct domain
  operations into one signature; prefer separate named operations or a
  configuration object.

## Deep modules

- Flag shallow pass-through helpers extracted merely to cut line count. Extract
  only when the new boundary protects an invariant, removes caller knowledge,
  isolates volatility, or creates a real testing seam.
- Flag core logic that knows about CLI parsing, HTTP framework objects, database
  row shapes, or vendor SDK response details instead of adapting at the boundary.
- Flag silent fallback to a wrong default on invalid input or unrecoverable
  state where a named exception raised at the boundary is the correct contract.

## Documentation

- Flag public modules, classes, functions, methods, and protocols whose purpose,
  parameters, return value, errors, or side effects are non-obvious from the name
  and signature yet carry no numpy-style docstring. Docstrings describe the
  contract and domain meaning (using `Parameters`/`Returns`/`Raises`/`Examples`
  where they clarify the contract), not implementation steps. Do not demand
  docstrings on self-evident helpers, and do not flag comments that state a
  non-obvious tradeoff, invariant, or ADR link.
