Rebuild a workspace's image from its ``setup_script``, unattended, and return at once.

This is the nightly reproducibility check, run on demand: a throwaway sandbox
boots from the base snapshot, runs the setup script and then the update
script, and the image is replaced only if both succeed. That takes minutes to
an hour, so this returns ``status: "started"`` with a ``task_id`` — the
workspace's image is not rebuilt yet. Read progress with
``background_task("status", task_id)``: the stage it reached and a tail of
the running script's live ``bash -x`` trace.

To *author* a workspace's image, do not use this — provision this thread's
sandbox with ordinary tools and ``publish_workspace`` it. A failed refresh
keeps the previous image, so runs never drop to the base snapshot because a
script broke.

Args:
    name: Name of a workspace already published with a ``setup_script``.

Returns:
    ``{"status": "started", "task_id": ...}``, or ``status: "error"`` with the
    reason it could not start.
