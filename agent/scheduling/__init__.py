"""Recurring work: the cron ritual, the scheduler task contract, and agent schedules.

Import the submodules directly. This package deliberately re-exports nothing:
:mod:`agent.scheduling.tasks` imports every job module, and several of those
job modules import :mod:`agent.scheduling.crons` to register their own cron.
"""
