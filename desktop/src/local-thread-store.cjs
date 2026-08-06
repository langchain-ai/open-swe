const fs = require("node:fs")
const path = require("node:path")

const THREAD_ID_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/
const THREAD_VERSION = 1

function threadPaths(storePath, threadId) {
  if (!THREAD_ID_PATTERN.test(threadId)) throw new Error("Invalid local thread ID")
  return {
    metadata: path.join(storePath, `${threadId}.json`),
    events: path.join(storePath, `${threadId}.events.jsonl`),
  }
}

function ensureStore(storePath) {
  fs.mkdirSync(storePath, { recursive: true, mode: 0o700 })
}

function metadata(session) {
  return {
    version: THREAD_VERSION,
    id: session.id,
    cwd: session.cwd,
    title: session.title,
    status: session.status,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    ...(session.providerSessionId
      ? { providerSessionId: session.providerSessionId }
      : {}),
    canResume: session.canResume === true,
  }
}

function writeMetadata(storePath, session) {
  ensureStore(storePath)
  const paths = threadPaths(storePath, session.id)
  const temporary = `${paths.metadata}.${process.pid}.tmp`
  fs.writeFileSync(temporary, `${JSON.stringify(metadata(session), null, 2)}\n`, {
    mode: 0o600,
  })
  fs.renameSync(temporary, paths.metadata)
}

function writeThread(storePath, session) {
  writeMetadata(storePath, session)
  const paths = threadPaths(storePath, session.id)
  if (!fs.existsSync(paths.events)) fs.writeFileSync(paths.events, "", { mode: 0o600 })
}

function appendThreadEvent(storePath, session, event) {
  ensureStore(storePath)
  const paths = threadPaths(storePath, session.id)
  fs.appendFileSync(paths.events, `${JSON.stringify(event)}\n`, { mode: 0o600 })
  if (["user-message", "run-start", "run-end", "error"].includes(event.type)) {
    writeMetadata(storePath, session)
  }
}

function parseMetadata(value) {
  if (
    !value ||
    value.version !== THREAD_VERSION ||
    typeof value.id !== "string" ||
    !THREAD_ID_PATTERN.test(value.id) ||
    typeof value.cwd !== "string" ||
    typeof value.title !== "string" ||
    typeof value.createdAt !== "number" ||
    typeof value.updatedAt !== "number"
  ) {
    return null
  }
  return {
    id: value.id,
    cwd: value.cwd,
    title: value.title,
    status: "stopped",
    createdAt: value.createdAt,
    updatedAt: value.updatedAt,
    ...(typeof value.providerSessionId === "string"
      ? { providerSessionId: value.providerSessionId }
      : {}),
    canResume: value.canResume === true,
  }
}

function readMetadata(storePath, threadId) {
  try {
    const paths = threadPaths(storePath, threadId)
    return parseMetadata(JSON.parse(fs.readFileSync(paths.metadata, "utf8")))
  } catch {
    return null
  }
}

function readEvents(storePath, threadId) {
  try {
    const paths = threadPaths(storePath, threadId)
    return fs
      .readFileSync(paths.events, "utf8")
      .split("\n")
      .filter(Boolean)
      .flatMap((line) => {
        try {
          const event = JSON.parse(line)
          return event && typeof event.sequence === "number" ? [event] : []
        } catch {
          return []
        }
      })
      .sort((left, right) => left.sequence - right.sequence)
  } catch {
    return []
  }
}

function readThread(storePath, threadId) {
  const summary = readMetadata(storePath, threadId)
  return summary ? { ...summary, events: readEvents(storePath, threadId) } : null
}

function readThreads(storePath) {
  try {
    return fs
      .readdirSync(storePath)
      .filter((name) => name.endsWith(".json"))
      .flatMap((name) => {
        const summary = readMetadata(storePath, name.slice(0, -5))
        return summary ? [summary] : []
      })
      .sort((left, right) => right.updatedAt - left.updatedAt)
  } catch {
    return []
  }
}

function removeThread(storePath, threadId) {
  const paths = threadPaths(storePath, threadId)
  for (const filePath of Object.values(paths)) {
    try {
      fs.rmSync(filePath)
    } catch (error) {
      if (error.code !== "ENOENT") throw error
    }
  }
}

module.exports = {
  appendThreadEvent,
  readThread,
  readThreads,
  removeThread,
  writeThread,
}
