"""Code the Open SWE backend and its Managed Deep Agents runtime (``mda/``) both run.

It belongs in MDA and will move there entirely. Until then the backend runs it too,
for the local threads it still runs in-process, which are being phased out, and for
the tool calls MDA still hands back to it. It holds no credentials and imports
nothing from either side, so each can depend on it.
"""
