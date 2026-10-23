import type { PluginPermission } from "@openswe/plugin-sdk"

export const PERMISSION_COPY: Record<
  PluginPermission,
  { label: string; description: string }
> = {
  storage: {
    label: "Keep data",
    description:
      "Save notes and comments, private to you or shared with everyone who can read a repository you share.",
  },
  "github:read": {
    label: "Read repositories",
    description:
      "Read files, branches, history and pull requests in the repositories you share, as you.",
  },
  "github:write": {
    label: "Change repositories",
    description:
      "Create branches, commit files and open pull requests in the repositories you share, as you.",
  },
  realtime: {
    label: "Work together live",
    description:
      "Share live edits, cursors and who is viewing with everyone using it on the same repository.",
  },
  agent: {
    label: "Work with an agent",
    description:
      "Offer tools to an agent you chat with beside it. You write every message; the agent works as you, in a private thread.",
  },
}

export function describePermission(permission: string): {
  label: string
  description: string
} {
  return Object.hasOwn(PERMISSION_COPY, permission)
    ? PERMISSION_COPY[permission as PluginPermission]
    : { label: permission, description: "" }
}
