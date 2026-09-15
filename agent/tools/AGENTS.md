# AGENTS.md

Exporting a tool from `__init__.py` does not enable it. A tool takes effect only once it is explicitly added to a graph in `agent/server.py` or `agent/reviewer.py`. Do not accumulate tools without that wiring and an authorization review of what the tool can reach.
