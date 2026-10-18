"""Every kind of slacklet Open SWE can start, by name."""

from openswe.slacklets.pull_request import PULL_REQUEST
from openswe.slacklets.store import AnySlackletKind

KINDS: dict[str, AnySlackletKind] = {kind.name: kind for kind in (PULL_REQUEST,)}
