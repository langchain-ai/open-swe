const fs = require("node:fs")
const path = require("node:path")

function projectName(cwd) {
  return path.basename(cwd) || cwd
}

function readProjects(filePath) {
  try {
    const value = JSON.parse(fs.readFileSync(filePath, "utf8"))
    if (!Array.isArray(value)) return []
    const projects = new Map()
    for (const item of value) {
      if (
        !item ||
        typeof item !== "object" ||
        typeof item.cwd !== "string" ||
        !path.isAbsolute(item.cwd)
      ) {
        continue
      }
      const cwd = path.normalize(item.cwd)
      projects.set(cwd, {
        cwd,
        name:
          typeof item.name === "string" && item.name.trim()
            ? item.name.trim()
            : projectName(cwd),
        addedAt:
          typeof item.addedAt === "number" && Number.isFinite(item.addedAt)
            ? item.addedAt
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

function writeProjects(filePath, projects) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  fs.writeFileSync(filePath, `${JSON.stringify(projects, null, 2)}\n`, {
    mode: 0o600,
  })
}

function addProject(filePath, cwd, now = Date.now()) {
  const normalized = fs.realpathSync(cwd)
  if (!fs.statSync(normalized).isDirectory()) {
    throw new Error("Choose a valid project directory")
  }
  const projects = readProjects(filePath)
  const existing = projects.find((project) => project.cwd === normalized)
  if (existing) return existing
  const project = { cwd: normalized, name: projectName(normalized), addedAt: now }
  writeProjects(filePath, [project, ...projects])
  return project
}

function removeProject(filePath, cwd) {
  const projects = readProjects(filePath)
  const remaining = projects.filter((project) => project.cwd !== cwd)
  if (remaining.length === projects.length) return false
  writeProjects(filePath, remaining)
  return true
}

module.exports = { addProject, readProjects, removeProject }
