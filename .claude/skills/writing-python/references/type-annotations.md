# Type Annotation Reference

## Nullable types — Optional[X]

```python
from typing import Optional

def get_user(user_id: str) -> Optional[str]:  # Correct
# NEVER: str | None
```

## Built-in generics — PEP 585

```python
items: list[str] = []           # NOT: List[str]
config: dict[str, Any] = {}     # NOT: Dict[str, Any]
pair: tuple[str, int]           # NOT: Tuple[str, int]
ids: set[str] = set()           # NOT: Set[str]
```

## Function types from collections.abc

```python
from collections.abc import Callable, Awaitable, Coroutine
from typing import Any, Generic, TypeVar
# NEVER: from typing import Callable, Awaitable
```

## TypeVar for generic functions

```python
T = TypeVar("T")

def with_retry(fn: Callable[[], T], *, max_retries: int = 3) -> T:
    ...  # Returns whatever type fn returns

async def async_with_retry(fn: Callable[[], Coroutine[Any, Any, T]], ...) -> T:
    ...
```

## TYPE_CHECKING for circular import prevention

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    ActionHandler = Callable[[ActionContext, EmailDraft], Awaitable[None]]
else:
    ActionHandler = Callable
```

## type: ignore — always with specific error code

```python
class AuthCheckMiddleware(AgentMiddleware):  # type: ignore[misc]
raise last_error  # type: ignore[misc]
# NEVER: # type: ignore (bare)
```

## Literal for exact string values

```python
from typing import Literal

def cache(ttl: Literal["5m", "1h"] = "1h") -> None: ...
move_type: Literal["joined", "departed"]
```

## Final for immutable constants

```python
from typing import Final

CONFIG_PATH: Final[Path] = Path(__file__).parent.parent / CONFIG_FILENAME
ALLOWED_TABLES: Final[frozenset[str]] = frozenset({"traces", "users", "accounts"})
```

## cast() for type-level assertions

```python
from typing import cast

result = cast(CompiledStateGraph, agent.with_config({...}))
config = cast(UserConfig, json.load(f))
data = cast(list[DripCampaign], result.data)
```

## Any — escape hatch, use sparingly

```python
data: dict[str, Any] = response.json()  # noqa — API boundary
# Better: define a TypedDict for known shapes
```

## TypeAlias vs NewType

```python
from typing import TypeAlias, NewType

# Shorthand (interchangeable):
TableAccessStatus: TypeAlias = dict[str, str]

# Branded type (NOT interchangeable):
UserDataMapping = NewType("UserDataMapping", dict[str, UserData])
```
