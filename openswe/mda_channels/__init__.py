"""What every MDA channel's backend side shares, whatever the provider.

An MDA channel (Linear's, GitHub's, ...) receives its provider's events in the MDA
deployment, asks the backend to prepare each run, and starts that run on a thread
it owns. The pieces here are the provider-neutral half of that:

- :mod:`.auth`: only the deployment, holding Open SWE's shared secret, may call a
  channel route.
- :mod:`.completion`: a channel run has no run-complete webhook, so its
  ``after_agent`` hook completes it, and the thread status reconcile job one that
  crashed (``openswe.threads.status``).

A thread or run belongs to a channel when its metadata names one
(``openswe.dispatch.channel_of``). The backend reads those threads as LangSmith
Studio does and never writes to them (``openswe.threads.runtimes``,
``openswe.threads.router``).
"""
