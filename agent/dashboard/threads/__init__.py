"""The dashboard's view of an agent thread, split by what each part answers.

- :mod:`.serialize` — what a thread looks like to the Agents UI.
- :mod:`.listing` — which threads a user sees, and in what order.
- :mod:`.runs` — starting, feeding, and stopping a thread's runs.
- :mod:`.proxy` — the transport under the LangGraph SDK protocol.
- :mod:`.sandbox` — reaching into a thread's sandbox for diffs and recovery.

Authorization is *not* here: who owns or may read a thread is
:mod:`agent.dashboard.authz`, shared with every other dashboard feature.
"""

__all__: list[str] = []
