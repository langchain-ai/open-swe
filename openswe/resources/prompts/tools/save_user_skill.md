Create or update a skill owned by the triggering user.

Call this when the user asks to create or modify a reusable skill.
The name must use lowercase letters, numbers, and single hyphens.
Changes apply to future runs and cannot affect another user's or a bundled skill.
Desktop-synced skills are read-only by default; edit their local files instead.
Pass `detach=true` only when the user explicitly asks to detach a synced skill for independent cloud editing; detaching permanently protects it from desktop updates and deletions.
