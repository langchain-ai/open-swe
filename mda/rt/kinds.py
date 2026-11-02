"""The kinds of Open SWE run this graph plays, and the rules each follows whatever the run.

A run's catalog names what the backend resolves for that run alone: its tools, models,
sandbox and grants, and the coding agent's loop rules, which depend on where it was asked
from. What is the same for every run of a kind lives here instead.
"""

from dataclasses import dataclass
from typing import Final, Literal

from runtime_api.catalog import Catalog, LoopRules

Graph = Literal["agent", "reviewer", "review-scout"]

# Reviews search through the shell; deepagents' own search tools are kept from the model.
_SHELL_SEARCH: Final = ["glob", "grep"]


@dataclass(frozen=True)
class KindRules:
    """What every run of a kind does around its model calls."""

    # None: each run's catalog names its own.
    loop: LoopRules | None = None
    # The most model calls a run makes; None: the catalog's, or else the runtime's.
    model_call_limit: int | None = None
    # Whether the run's shell commands go to the backend's guards first (``check_shell``).
    guards_shell: bool = False

    def applied(self, catalog: Catalog) -> Catalog:
        """``catalog``, with this kind's rules in place of the run's."""
        loop = self.loop if self.loop is not None else catalog.loop
        github_proxy = catalog.sandbox.github_proxy if catalog.sandbox is not None else None
        loop = loop.model_copy(
            update={
                "deployment_prompt": catalog.loop.deployment_prompt,
                # MDA puts a fresh token in this sandbox's proxy config itself, before each command.
                "refresh_github_proxy": loop.refresh_github_proxy and github_proxy is None,
            }
        )
        files = catalog.files
        if self.guards_shell and "shell_commands" not in files.guards:
            files = files.model_copy(update={"guards": [*files.guards, "shell_commands"]})
        return catalog.model_copy(
            update={
                "loop": loop,
                "files": files,
                "model_call_limit": self.model_call_limit or catalog.model_call_limit,
            }
        )


KINDS: Final[dict[Graph, KindRules]] = {
    "agent": KindRules(guards_shell=True),
    "reviewer": KindRules(
        loop=LoopRules(
            hidden_tools=_SHELL_SEARCH, refresh_github_proxy=True, at_end=["settle_review_check"]
        ),
        model_call_limit=5_000,
    ),
    "review-scout": KindRules(
        loop=LoopRules(
            hidden_tools=_SHELL_SEARCH,
            # Without messages from the people who asked for the PR, the model would
            # summarize its description instead.
            hidden_unless={"has_human_input": ["record_human_input"]},
            stop_when=["scout_nothing_to_plan"],
            at_end=["finish_scout_plan"],
        ),
        model_call_limit=150,
    ),
}
