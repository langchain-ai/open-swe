"""Every kind of slacklet: the built-in ones by name, and the ones agents define."""

from uuid import UUID

from openswe.slacklets.dynamic import DYNAMIC_KIND, SlackletDefinition
from openswe.slacklets.pull_request import PULL_REQUEST
from openswe.slacklets.store import AnySlackletKind, Slacklet

KINDS: dict[str, AnySlackletKind] = {kind.name: kind for kind in (PULL_REQUEST,)}


async def kind_of(slacklet: Slacklet) -> AnySlackletKind | None:
    """The kind a stored slacklet was made with, loading an agent's program when it has one."""
    if slacklet.kind != DYNAMIC_KIND:
        return KINDS.get(slacklet.kind)
    try:
        definition = await SlackletDefinition.get(UUID(slacklet.subject))
    except ValueError:
        return None
    return definition.kind() if definition else None
