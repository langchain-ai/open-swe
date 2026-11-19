Move every install of an app that a person added or changed to one of its releases, its newest by default.
Use only when an admin explicitly asks to move everyone onto a release; pass the app's id and, optionally, a release number.
Installs on that release or a newer one stay where they are and count as `current`; no install ever moves back to an older release.
An install moves only when the release asks for no permission its current release didn't; the rest stay for their person to approve, counted as `waiting`.
Installs Robin added for a featured app follow the release featured under Apps for everyone instead; update that to move them.
Requires a currently authorized admin, and the change is audited.
