import fs from "node:fs"
import os from "node:os"
import path from "node:path"

import { getConfig } from "@langchain/langgraph"
import {
  type BackendFactory,
  CompositeBackend,
  FilesystemBackend,
  LocalShellBackend,
} from "deepagents"

const SHELL_ENV_KEYS = [
  "HOME",
  "LANG",
  "LC_ALL",
  "PATH",
  "SHELL",
  "TMPDIR",
] as const

export interface LocalWorkspaceConfig {
  localProjectPath?: unknown
  threadId?: unknown
}

type Environment = Readonly<Record<string, string | undefined>>

function allowlistedPaths(value: unknown): Set<string> {
  if (!Array.isArray(value)) {
    throw new Error("OPEN_SWE_LOCAL_PROJECTS_FILE must contain a JSON array")
  }

  const allowed = new Set<string>()
  for (const entry of value) {
    const candidate =
      typeof entry === "string"
        ? entry
        : entry &&
            typeof entry === "object" &&
            "cwd" in entry &&
            typeof entry.cwd === "string"
          ? entry.cwd
          : null
    if (!candidate) continue
    try {
      allowed.add(fs.realpathSync(candidate))
    } catch {
      // Stale project entries do not authorize a future path at the same name.
    }
  }
  return allowed
}

export function resolveLocalProject(
  configurable: LocalWorkspaceConfig,
  environment: Environment = process.env
): string {
  const requested = configurable.localProjectPath
  const allowlistPath = environment.OPEN_SWE_LOCAL_PROJECTS_FILE
  if (typeof requested !== "string" || !requested || !allowlistPath) {
    throw new Error("Local runs require an allowlisted local_project_path")
  }

  let entries: unknown
  try {
    entries = JSON.parse(fs.readFileSync(allowlistPath, "utf8"))
  } catch (cause) {
    throw new Error("Could not read the local project allowlist", {
      cause,
    })
  }

  let project: string
  try {
    project = fs.realpathSync(requested)
  } catch (cause) {
    throw new Error("local_project_path is not an allowed project directory", {
      cause,
    })
  }

  if (!allowlistedPaths(entries).has(project) || !fs.statSync(project).isDirectory()) {
    throw new Error("local_project_path is not an allowed project directory")
  }
  return project
}

export function sanitizeShellEnvironment(
  environment: Environment = process.env
): Record<string, string> {
  return Object.fromEntries(
    SHELL_ENV_KEYS.flatMap((key) => {
      const value = environment[key]
      return value ? [[key, value]] : []
    })
  )
}

function safeThreadId(value: unknown): string {
  const normalized = String(value || "thread")
    .replace(/[^A-Za-z0-9._-]/g, "-")
    .replace(/^\.+/, "")
  return normalized || "thread"
}

function artifactsRoot(environment: Environment): string {
  return (
    environment.OPEN_SWE_LOCAL_ARTIFACTS_DIR ??
    path.join(os.tmpdir(), `open-swe-artifacts-${process.getuid?.() ?? "user"}`)
  )
}

export async function createLocalWorkspace(
  configurable: LocalWorkspaceConfig,
  environment: Environment = process.env
): Promise<CompositeBackend> {
  const project = resolveLocalProject(configurable, environment)
  // Real host paths, not a virtual root: the agent reads skills, configs, and
  // tooling that live outside the project, and shell commands never saw the
  // virtual root anyway.
  const shell = await LocalShellBackend.create({
    rootDir: project,
    virtualMode: false,
    env: sanitizeShellEnvironment(environment),
    inheritEnv: false,
  })

  const threadRoot = path.join(
    artifactsRoot(environment),
    safeThreadId(configurable.threadId)
  )
  const routes: Record<string, FilesystemBackend> = {}
  for (const name of ["large_tool_results", "conversation_history"] as const) {
    const directory = path.join(threadRoot, name)
    await fs.promises.mkdir(directory, { recursive: true })
    routes[`/${name}/`] = new FilesystemBackend({
      rootDir: directory,
      virtualMode: true,
    })
  }

  return new CompositeBackend(shell, routes)
}

export function createLocalWorkspaceBackend(
  environment: Environment = process.env
): BackendFactory {
  return (runtime) => {
    const configurable = runtime.configurable ?? getConfig()?.configurable ?? {}
    return createLocalWorkspace(
      {
        localProjectPath: configurable.local_project_path,
        threadId: configurable.thread_id,
      },
      environment
    )
  }
}
