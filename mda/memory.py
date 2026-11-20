"""Robin's memory: each person's own layer and Robin's shared layer, mounted wherever only
people in the company can see the conversation. MDA mounts a person's layer only for that
person. What each run may read and save is ``rt.memory_policy``'s."""

from managed_deepagents import ManagedChannelContext, MemoryLayer, define_memory
from rt.backend import RunContext
from runtime_api.catalog import MemoryScope


def memory_is_internal(context: RunContext | ManagedChannelContext) -> bool:
    """Mount a layer only where only people in the company can see the run. MDA gives a run
    its channel delivered only the channel, not the scope, so a Slack one mounts and
    ``rt.memory_policy`` checks every call against the scope; any other channel, like
    GitHub's, may be public, so it never mounts."""
    if isinstance(context, ManagedChannelContext):
        return context.channel["provider"] == "slack"
    scope = (context.catalog or {}).get("memory")
    return scope is not None and MemoryScope.model_validate(scope).internal


memory = define_memory(
    agent=MemoryLayer(allow=memory_is_internal),
    user=MemoryLayer(allow=memory_is_internal),
)
