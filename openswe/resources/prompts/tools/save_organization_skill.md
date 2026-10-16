Create or update an organization skill, for workspace admins only.

Organization skills are loaded into every user's runs, so confirm the name and
wording with the user before saving. Instructions are a full replacement of the
skill body, not a delta. Existing skills are readable under
``/organization-skills/``.

Args:
    name: Skill name using lowercase letters, numbers, and single hyphens.
    description: One-line summary telling an agent when the skill applies.
    instructions: The skill's full ``SKILL.md`` body.

Returns:
    ``{"ok": True, "skill": {...}, "created": bool}``.
