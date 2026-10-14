"""Open SWE's live transcript stream, shared by the backend and remote agent runtimes.

``stream`` turns a model call's streaming deltas into transcript fragments,
``wire`` is what a remote runtime sends the backend, and ``remote`` is the
runtime side: the middleware that captures a remotely hosted agent's replies
and the sink that posts them to the backend.
"""
