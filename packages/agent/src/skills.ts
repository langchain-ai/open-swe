import fs from "node:fs"
import os from "node:os"
import path from "node:path"

import { FilesystemBackend, createSkillsMiddleware } from "deepagents"

type SkillsMiddleware = ReturnType<typeof createSkillsMiddleware>

/**
 * Skill directories in ascending precedence, mirroring deepagents-code: a later
 * source wins when two directories define the same skill name.
 */
export function skillSources(
  project: string | null,
  home: string = os.homedir()
): string[] {
  const candidates = [
    path.join(home, ".deepagents", "skills"),
    path.join(home, ".agents", "skills"),
    ...(project
      ? [
          path.join(project, ".deepagents", "skills"),
          path.join(project, ".agents", "skills"),
        ]
      : []),
    path.join(home, ".claude", "skills"),
    ...(project ? [path.join(project, ".claude", "skills")] : []),
  ]
  return candidates.filter((directory) => {
    try {
      return fs.statSync(directory).isDirectory()
    } catch {
      return false
    }
  })
}

export function createLocalSkillsMiddleware(
  project: string | null
): SkillsMiddleware | null {
  const sources = skillSources(project)
  if (!sources.length) return null
  return createSkillsMiddleware({
    backend: new FilesystemBackend({ virtualMode: false }),
    sources: sources.map((directory) => `${directory}/`),
  })
}
