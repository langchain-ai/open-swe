"""Stored configuration: what a team, a user, and a repository have chosen.

Everything Open SWE persists about *how* it should behave — models and
reasoning effort, per-user profiles and instructions, third-party credentials,
enabled repositories, environments and snapshots, plans, skills, review styles,
usage. All of it is backed by :mod:`agent.store` and none of it knows about
HTTP: the graphs, tools and webhooks read it directly, and the dashboard is
just one more caller that happens to expose it over a REST API.
"""
