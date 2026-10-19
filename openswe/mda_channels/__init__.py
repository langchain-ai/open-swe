"""What every MDA channel's backend side shares, whatever the provider.

An MDA channel (Linear's, GitHub's, ...) receives its provider's events in the MDA
deployment, asks the backend's endpoint for that channel what each one starts, and
starts that run on a thread it owns, with the backend's run-complete webhook. Only
the deployment, holding Open SWE's shared secret, may call a channel endpoint
(:mod:`.auth`).

A thread or run belongs to a channel when its metadata names one
(``openswe.dispatch.channel_of``). The backend reads those threads as LangSmith
Studio does and never writes to them (``openswe.threads.runtimes``,
``openswe.threads.router``).
"""
