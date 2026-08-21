"""Sandbox provider integrations.

Deliberately empty: each provider module imports the SDK of one platform, and a
deployment installs only the platform it runs on. ``agent.utils.sandbox``
imports the configured one by name.
"""
