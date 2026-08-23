import { randomUUID } from "node:crypto"
import fs from "node:fs"
import path from "node:path"

export interface LocalProject {
  cwd: string
  name: string
  addedAt: number
}

function projectName(cwd: string): string {
  return path.basename(cwd) || cwd
}

/**
 * Where the projects live. The desktop app points this at its user data; a
 * standalone server takes it from the environment.
 */
export function projectsFile(
  environment: NodeJS.ProcessEnv = process.env
): string | null {
  return environment.OPEN_SWE_LOCAL_PROJECTS_FILE || null
}

export function readProjects(filePath: string): Array<LocalProject> {
  try {
    const value: unknown = JSON.parse(fs.readFileSync(filePath, "utf8"))
    if (!Array.isArray(value)) return []
    const projects = new Map<string, LocalProject>()
    for (const item of value) {
      if (
        !item ||
        typeof item !== "object" ||
        typeof (item as LocalProject).cwd !== "string" ||
        !path.isAbsolute((item as LocalProject).cwd)
      ) {
        continue
      }
      const entry = item as Partial<LocalProject> & { cwd: string }
      const cwd = path.normalize(entry.cwd)
      projects.set(cwd, {
        cwd,
        name:
          typeof entry.name === "string" && entry.name.trim()
            ? entry.name.trim()
            : projectName(cwd),
        addedAt:
          typeof entry.addedAt === "number" && Number.isFinite(entry.addedAt)
            ? entry.addedAt
            : 0,
      })
    }
    return [...projects.values()].sort(
      (left, right) => right.addedAt - left.addedAt
    )
  } catch {
    return []
  }
}

function writeProjects(filePath: string, projects: Array<LocalProject>): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(projects, null, 2)}\n`, {
      mode: 0o600,
    })
    fs.renameSync(temporary, filePath)
  } finally {
    fs.rmSync(temporary, { force: true })
  }
}

export function addProject(
  filePath: string,
  cwd: string,
  now = Date.now()
): LocalProject {
  const normalized = fs.realpathSync(cwd)
  if (!fs.statSync(normalized).isDirectory()) {
    throw new Error("Choose a valid project directory")
  }
  const projects = readProjects(filePath)
  const existing = projects.find((project) => project.cwd === normalized)
  if (existing) return existing
  const project = {
    cwd: normalized,
    name: projectName(normalized),
    addedAt: now,
  }
  writeProjects(filePath, [project, ...projects])
  return project
}

export function removeProject(filePath: string, cwd: string): boolean {
  const projects = readProjects(filePath)
  const remaining = projects.filter((project) => project.cwd !== cwd)
  if (remaining.length === projects.length) return false
  writeProjects(filePath, remaining)
  return true
}
