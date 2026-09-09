Snapshot this sandbox as the named environment's image.

Everything currently on this sandbox's filesystem is captured, so provision it
fully first (clone the repos, install toolchains, warm caches) and leave no
secrets or tokens on disk. The snapshot replaces the environment's previous
one once it is ready. Capture takes minutes on a large filesystem.

New sandboxes boot from it only for the environment named ``default``.

Args:
    name: Name of an environment already saved with ``save_environment``.

Returns:
    ``{"ok": True, "environment": {...}}`` with the new snapshot id.
