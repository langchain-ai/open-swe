const fs = require("node:fs")
const path = require("node:path")
const { randomUUID } = require("node:crypto")

function validString(value) {
  return typeof value === "string" && value.length > 0
}

function validRecord(value) {
  if (
    !value ||
    typeof value !== "object" ||
    !validString(value.id) ||
    !validString(value.acpSessionId) ||
    !validString(value.cwd) ||
    !path.isAbsolute(value.cwd) ||
    !validString(value.title) ||
    !Number.isFinite(value.createdAt) ||
    !Number.isFinite(value.updatedAt)
  ) {
    return false
  }
  if (value.modelId !== undefined && !validString(value.modelId)) return false
  if (value.effort !== undefined && !validString(value.effort)) return false
  if (value.dcodeCommand !== undefined && !validString(value.dcodeCommand)) return false
  return (
    value.checkpoint === undefined ||
    (value.checkpoint &&
      typeof value.checkpoint === "object" &&
      validString(value.checkpoint.repo) &&
      path.isAbsolute(value.checkpoint.repo) &&
      value.checkpoint.ref ===
        `refs/open-swe/local/${value.id.replace(/[^A-Za-z0-9._-]/g, "-")}`)
  )
}

function readAcpSessions(filePath) {
  try {
    const value = JSON.parse(fs.readFileSync(filePath, "utf8"))
    if (!Array.isArray(value)) return []
    const sessions = new Map()
    for (const session of value) {
      if (validRecord(session)) {
        sessions.set(session.id, { ...session, cwd: path.normalize(session.cwd) })
      }
    }
    return [...sessions.values()]
  } catch {
    return []
  }
}

function writeAcpSessions(filePath, sessions) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  const temporary = `${filePath}.${randomUUID()}.tmp`
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(sessions, null, 2)}\n`, {
      mode: 0o600,
    })
    fs.renameSync(temporary, filePath)
    fs.chmodSync(filePath, 0o600)
  } finally {
    fs.rmSync(temporary, { force: true })
  }
}

module.exports = { readAcpSessions, writeAcpSessions }
