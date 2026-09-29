import * as z from "zod"

import type { ApiClient } from "./api.ts"

type Access = "session" | "admin"
type Input = Record<string, z.ZodType>

export interface ExposedTool {
  name: string
  access: Access
  title: string
  description: string
  inputSchema: Input
  readOnly: boolean
  run: (api: ApiClient, args: Record<string, unknown>) => Promise<unknown>
}

function endpoint(
  name: string,
  access: Access,
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  route: string,
  title: string,
  description: string,
  inputSchema: Input = {},
  bodyKeys: readonly string[] = []
): ExposedTool {
  return {
    name,
    access,
    title,
    description,
    inputSchema,
    readOnly: method === "GET",
    run: (api, args) => {
      const path = route.replace(/\{(\w+)\}/g, (_, field: string) =>
        encodeURIComponent(String(args[field]))
      )
      const body = Object.fromEntries(
        bodyKeys.flatMap((key) =>
          args[key] === undefined ? [] : [[key, args[key]]]
        )
      )
      return api.mcpRequest(method, path, bodyKeys.length ? body : undefined)
    },
  }
}

const name = z.string().min(1)
const skillBody = {
  name,
  description: z.string().min(1),
  instructions: z.string().default(""),
}
const updateSkill = {
  description: skillBody.description,
  instructions: skillBody.instructions,
}
const workspaceBody = {
  name,
  prompt: z.string().optional(),
  repos: z.array(z.string()).optional(),
  setup_script: z.string().optional(),
  update_script: z.string().optional(),
  base_snapshot_id: z.string().nullable().optional(),
  snapshot_name: z.string().nullable().optional(),
  slack_channel_ids: z.array(z.string()).optional(),
  kitchen_channel_ids: z.array(z.string()).optional(),
  mem_bytes: z.number().int().positive().optional(),
  vcpus: z.number().int().positive().optional(),
  fs_capacity_bytes: z.number().int().positive().optional(),
  create_params: z.record(z.string(), z.json()).optional(),
}

export const exposedTools: readonly ExposedTool[] = [
  endpoint(
    "get_thread",
    "session",
    "GET",
    "/threads/{thread_id}?mark_viewed=false",
    "Get thread",
    "Read a thread you can access without marking it viewed.",
    { thread_id: name }
  ),
  endpoint(
    "list_workspaces",
    "admin",
    "GET",
    "/workspaces",
    "List workspaces",
    "List all workspace definitions and snapshot status."
  ),
  endpoint(
    "get_workspace",
    "admin",
    "GET",
    "/workspaces/{slug}",
    "Get workspace",
    "Read a workspace definition and snapshot status.",
    { slug: name }
  ),
  endpoint(
    "create_workspace",
    "admin",
    "POST",
    "/workspaces",
    "Create workspace",
    "Create a workspace with its initial definition.",
    workspaceBody,
    Object.keys(workspaceBody)
  ),
  endpoint(
    "update_workspace",
    "admin",
    "PUT",
    "/workspaces/{slug}",
    "Update workspace",
    "Change a workspace definition.",
    { slug: name, ...workspaceBody },
    Object.keys(workspaceBody)
  ),
  endpoint(
    "refresh_workspace",
    "admin",
    "POST",
    "/workspaces/{slug}/refresh",
    "Refresh workspace",
    "Start a workspace snapshot rebuild.",
    { slug: name }
  ),
  endpoint(
    "delete_workspace",
    "admin",
    "DELETE",
    "/workspaces/{slug}",
    "Delete workspace",
    "Delete a workspace and its settings.",
    { slug: name }
  ),
  endpoint(
    "list_workspace_repositories",
    "admin",
    "GET",
    "/workspaces/{slug}/repositories",
    "List workspace repositories",
    "List repository settings for a workspace.",
    { slug: name }
  ),
  endpoint(
    "configure_workspace_repository",
    "admin",
    "PUT",
    "/workspaces/{slug}/repositories/{owner}/{name}",
    "Configure workspace repository",
    "Set whether a repository can start workspace threads.",
    { slug: name, owner: name, name, may_start_threads: z.boolean() },
    ["may_start_threads"]
  ),
  endpoint(
    "list_automations",
    "session",
    "GET",
    "/schedules",
    "List automations",
    "List workspace automations."
  ),
  endpoint(
    "create_automation",
    "admin",
    "POST",
    "/schedules",
    "Create automation",
    "Create a workspace automation.",
    {
      prompt: name,
      workspace: name,
      schedule: z.string().optional(),
      trigger: z.enum(["schedule", "github_issue_opened"]).optional(),
      repo: z.string().optional(),
      name: z.string().optional(),
      admin_thread: z.boolean().optional(),
    },
    [
      "prompt",
      "workspace",
      "schedule",
      "trigger",
      "repo",
      "name",
      "admin_thread",
    ]
  ),
  endpoint(
    "update_automation",
    "admin",
    "PATCH",
    "/schedules/{schedule_id}",
    "Update automation",
    "Change an existing workspace automation.",
    {
      schedule_id: name,
      prompt: z.string().optional(),
      schedule: z.string().optional(),
      enabled: z.boolean().optional(),
      name: z.string().optional(),
      workspace: z.string().optional(),
      repo: z.string().nullable().optional(),
    },
    ["prompt", "schedule", "enabled", "name", "workspace", "repo"]
  ),
  endpoint(
    "trigger_automation",
    "admin",
    "POST",
    "/schedules/{schedule_id}/trigger",
    "Trigger automation",
    "Start an automation now.",
    { schedule_id: name }
  ),
  endpoint(
    "delete_automation",
    "admin",
    "DELETE",
    "/schedules/{schedule_id}",
    "Delete automation",
    "Delete a workspace automation.",
    { schedule_id: name }
  ),
  endpoint(
    "list_skills",
    "session",
    "GET",
    "/skills",
    "List personal skills",
    "List the signed-in person's skills."
  ),
  endpoint(
    "create_skill",
    "session",
    "POST",
    "/skills",
    "Create personal skill",
    "Create a skill for the signed-in person.",
    skillBody,
    Object.keys(skillBody)
  ),
  endpoint(
    "update_skill",
    "session",
    "PUT",
    "/skills/{name}",
    "Update personal skill",
    "Update a skill owned by the signed-in person.",
    { name, ...updateSkill },
    Object.keys(updateSkill)
  ),
  endpoint(
    "delete_skill",
    "session",
    "DELETE",
    "/skills/{name}",
    "Delete personal skill",
    "Delete a skill owned by the signed-in person.",
    { name }
  ),
  endpoint(
    "list_organization_skills",
    "session",
    "GET",
    "/organization-skills",
    "List organization skills",
    "List organization-wide skills."
  ),
  endpoint(
    "create_organization_skill",
    "admin",
    "POST",
    "/organization-skills",
    "Create organization skill",
    "Create an organization-wide skill.",
    skillBody,
    Object.keys(skillBody)
  ),
  endpoint(
    "update_organization_skill",
    "admin",
    "PUT",
    "/organization-skills/{name}",
    "Update organization skill",
    "Update an organization-wide skill.",
    { name, ...updateSkill },
    Object.keys(updateSkill)
  ),
  endpoint(
    "delete_organization_skill",
    "admin",
    "DELETE",
    "/organization-skills/{name}",
    "Delete organization skill",
    "Delete an organization-wide skill.",
    { name }
  ),
  endpoint(
    "get_my_instructions",
    "session",
    "GET",
    "/me/instructions",
    "Get personal instructions",
    "Read your agent instructions."
  ),
  endpoint(
    "set_my_instructions",
    "session",
    "PUT",
    "/me/instructions",
    "Set personal instructions",
    "Replace your agent instructions.",
    { instructions: z.string().max(20_000) },
    ["instructions"]
  ),
  endpoint(
    "delete_my_instructions",
    "session",
    "DELETE",
    "/me/instructions",
    "Delete personal instructions",
    "Delete your agent instructions."
  ),
  endpoint(
    "get_my_preferences",
    "session",
    "GET",
    "/me/preferences",
    "Get personal preferences",
    "Read your dashboard preferences."
  ),
  endpoint(
    "set_my_preferences",
    "session",
    "PUT",
    "/me/preferences",
    "Set personal preferences",
    "Set your dashboard preferences.",
    {
      default_visibility: z.enum(["public", "private"]),
      default_workspace: z.string().nullable().optional(),
      local_tracing_project: z.string().nullable().optional(),
      follow_up_behavior: z.enum(["queue", "steer"]).optional(),
    },
    [
      "default_visibility",
      "default_workspace",
      "local_tracing_project",
      "follow_up_behavior",
    ]
  ),
  endpoint(
    "list_instance_mcps",
    "admin",
    "GET",
    "/mcps",
    "List instance MCP connections",
    "List instance-wide MCP connection settings without revealing credentials."
  ),
  endpoint(
    "list_workspace_mcps",
    "admin",
    "GET",
    "/workspaces/{workspace}/mcps",
    "List workspace MCP connections",
    "List a workspace's MCP connection settings without revealing credentials.",
    { workspace: name }
  ),
  endpoint(
    "list_user_mcps",
    "session",
    "GET",
    "/my-mcps",
    "List personal MCP connections",
    "List your personal MCP connection settings without revealing credentials."
  ),
]
