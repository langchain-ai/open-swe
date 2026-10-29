Run an app you are building in the sandbox beside this thread, in the person's web UI.

An app is one self-contained HTML file that embeds its manifest; read the
``web-apps`` skill before building one. Build it, then pass the built file
here: the person sees it live in a panel beside the thread, and each call
replaces the build they see. Call it again after every change worth looking at.

From that panel the person chats with you in this same thread, with the tools
the app provides and the context it shares.

A build asking for permissions the running preview does not have waits for
the person to approve them in the panel.

Args:
    bundle_path: The built ``.html`` file in the sandbox.

Returns:
    ``{success: True, previewing: True}`` once it runs, ``{success: True,
    previewing: False, permissions}`` while it waits for approval, or
    ``{success: False, error}`` when the file is not an app.
