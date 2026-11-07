const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

const PROJECT_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function projectName(cwd) {
  return path.basename(cwd) || cwd;
}

/** The Robin project a folder belongs to; the path itself never leaves this Mac. */
function projectIdOf(value) {
  return typeof value === "string" && PROJECT_ID.test(value) ? value : null;
}

function readProjects(filePath) {
  try {
    const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!Array.isArray(value)) return [];
    const projects = new Map();
    for (const item of value) {
      if (
        !item ||
        typeof item !== "object" ||
        typeof item.cwd !== "string" ||
        !path.isAbsolute(item.cwd)
      ) {
        continue;
      }
      let cwd;
      try {
        cwd = fs.realpathSync(item.cwd);
        if (!fs.statSync(cwd).isDirectory()) continue;
      } catch {
        continue;
      }
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
        projectId: projectIdOf(item.projectId),
      });
    }
    return [...projects.values()].sort(
      (left, right) => right.addedAt - left.addedAt,
    );
  } catch {
    return [];
  }
}

function writeProjects(filePath, projects) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(projects, null, 2)}\n`, {
      mode: 0o600,
    });
    fs.renameSync(temporary, filePath);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function addProject(filePath, cwd, now = Date.now()) {
  const normalized = fs.realpathSync(cwd);
  if (!fs.statSync(normalized).isDirectory()) {
    throw new Error("Choose a valid project directory");
  }
  const projects = readProjects(filePath);
  const existing = projects.find((project) => project.cwd === normalized);
  if (existing) return existing;
  const project = {
    cwd: normalized,
    name: projectName(normalized),
    addedAt: now,
    projectId: null,
  };
  writeProjects(filePath, [project, ...projects]);
  return project;
}

function removeProject(filePath, cwd) {
  const projects = readProjects(filePath);
  const remaining = projects.filter((project) => project.cwd !== cwd);
  if (remaining.length === projects.length) return false;
  writeProjects(filePath, remaining);
  return true;
}

function linkProject(filePath, cwd, projectId) {
  const linked = projectIdOf(projectId);
  if (projectId !== null && !linked) throw new Error("Not a project id");
  const projects = readProjects(filePath);
  const project = projects.find((item) => item.cwd === cwd);
  if (!project) return null;
  project.projectId = linked;
  writeProjects(filePath, projects);
  return project;
}

module.exports = { addProject, linkProject, readProjects, removeProject };
