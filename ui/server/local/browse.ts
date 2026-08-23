import fs from "node:fs"
import os from "node:os"
import path from "node:path"

export interface DirectoryListing {
  path: string
  parent: string | null
  entries: Array<{ name: string; path: string; isRepository: boolean }>
}

export function listDirectories(
  requested: string | null,
  home = os.homedir()
): DirectoryListing {
  const target = path.resolve(requested?.trim() || home)
  const parent = path.dirname(target)
  const entries: DirectoryListing["entries"] = []
  for (const entry of fs.readdirSync(target, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith(".")) continue
    const absolute = path.join(target, entry.name)
    entries.push({
      name: entry.name,
      path: absolute,
      isRepository: fs.existsSync(path.join(absolute, ".git")),
    })
  }
  entries.sort((left, right) => left.name.localeCompare(right.name))
  return { path: target, parent: parent === target ? null : parent, entries }
}
