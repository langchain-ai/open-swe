import type { PluginPermission } from "@openswe/plugin-sdk"

export const PERMISSION_COPY: Record<
  PluginPermission,
  { label: string; description: string }
> = {
  storage: {
    label: "Keep data",
    description:
      "Save notes and comments, private to you or shared with everyone who can read one of its repositories.",
  },
  "github:read": {
    label: "Read repositories",
    description:
      "Read files, branches, history and pull requests in its repositories, as you.",
  },
  "github:write": {
    label: "Change repositories",
    description:
      "Create branches, commit files and open pull requests in its repositories, as you.",
  },
  threads: {
    label: "Start agent threads",
    description:
      "Ask you to start an agent thread in one of its repositories. You read and approve every request first.",
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
