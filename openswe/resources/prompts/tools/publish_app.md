Release the app built in the sandbox as a new version, for the person to keep
in their web UI's sidebar.

Publish only when the person asks to keep or ship the app. A release is
immutable; publishing again makes the next version. It is added to the
person's sidebar, or replaces the version there, when it asks for no
permissions beyond those they already approved for this app; otherwise they add
it from Settings → Apps.

Args:
    bundle_path: The built ``.html`` file in the sandbox.

Returns:
    ``{success: True, app, number, installed}``, with ``next`` saying what
    the person does when it was not installed, or ``{success: False, error}``.
