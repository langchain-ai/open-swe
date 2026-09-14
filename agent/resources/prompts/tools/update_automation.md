Update a workspace automation, preserving omitted fields.

Automations share one thread and sandbox by default, so files written in the sandbox persist between runs. Set ``thread_mode`` to ``new`` for a fresh sandbox and history on every run. Use ``clear_repo`` or ``clear_slack_channel`` to remove those destinations.
