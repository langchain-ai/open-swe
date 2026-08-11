const { execFile, execFileSync } = require("node:child_process")
const { randomBytes } = require("node:crypto")
const fs = require("node:fs")
const path = require("node:path")

const DEFAULT_COLS = 120
const DEFAULT_ROWS = 30
const HISTORY_LINES = 5_000
const HISTORY_CHARS = 2_000_000
const MAX_SESSIONS = 128
const KILL_GRACE_MS = 1_000
const INTERNAL_ENV = new Set([
  "ELECTRON_RENDERER_PORT",
  "ELECTRON_RUN_AS_NODE",
  "NODE_OPTIONS",
  "PORT",
  "PWD",
  "OLDPWD",
  "SHLVL",
])
const APPIMAGE_ENV = ["APPIMAGE", "APPDIR", "ARGV0", "OWD"]
const APPIMAGE_PATH_ENV = ["PATH", "LD_LIBRARY_PATH", "XDG_DATA_DIRS", "GSETTINGS_SCHEMA_DIR"]
const MISSING_EXECUTABLE = ["posix_spawnp failed", "enoent", "not found", "file not found", "no such file"]
let configuredManager = null
let shellEnv = null

function ensurePtySpawnHelperExecutable(options = {}) {
  const platform = options.platform || process.platform
  if (platform === "win32") return
  try {
    const fileSystem = options.fs || fs
    const unixTerminalPath = options.unixTerminalPath || require.resolve("node-pty/lib/unixTerminal.js")
    const packageDir = path.resolve(path.dirname(unixTerminalPath), "..")
      .replace("app.asar", "app.asar.unpacked")
      .replace("node_modules.asar", "node_modules.asar.unpacked")
    const candidates = [
      path.join(packageDir, "prebuilds", `${platform}-${options.arch || process.arch}`, "spawn-helper"),
      path.join(packageDir, "build", "Release", "spawn-helper"),
      path.join(packageDir, "build", "Debug", "spawn-helper"),
    ]
    for (const helperPath of candidates) {
      try {
        const mode = fileSystem.statSync(helperPath).mode & 0o777
        if ((mode & 0o111) === 0) fileSystem.chmodSync(helperPath, mode | 0o755)
      } catch {}
    }
  } catch {}
}

function getUserShellEnv() {
  if (shellEnv) return shellEnv
  const shell = process.env.SHELL || "/bin/zsh"
  for (const args of [["-il", "-c"], ["-l", "-c"], ["-i", "-c"]]) {
    try {
      const mark = randomBytes(8).toString("hex")
      const result = execFileSync(shell, [...args, `echo '${mark}'; env; echo '${mark}'`], {
        encoding: "utf8",
        timeout: 10_000,
        stdio: ["pipe", "pipe", "pipe"],
      })
      const start = result.indexOf(mark)
      const end = result.lastIndexOf(mark)
      if (start === -1 || start === end) continue
      shellEnv = { ...process.env }
      for (const line of result.slice(start + mark.length, end).split("\n")) {
        const separator = line.indexOf("=")
        if (separator > 0) shellEnv[line.slice(0, separator)] = line.slice(separator + 1)
      }
      return shellEnv
    } catch {}
  }
  shellEnv = { ...process.env }
  return shellEnv
}

function shellCandidates(env, platform = process.platform) {
  const candidates =
    platform === "win32"
      ? [env.SHELL, "pwsh.exe", "powershell.exe", env.ComSpec, "cmd.exe"]
      : [env.SHELL, "/bin/zsh", "/bin/bash", "/bin/sh", "zsh", "bash", "sh"]
  return candidates
    .filter(Boolean)
    .map((shell) => (platform === "win32" ? shell : shell.trim().split(/\s+/)[0]))
    .filter((value, index, values) => values.indexOf(value) === index)
    .map((shell) => ({
      shell,
      args:
        platform === "win32"
          ? /powershell|pwsh/i.test(shell)
            ? ["-NoLogo"]
            : []
          : path.basename(shell) === "zsh"
            ? ["-o", "nopromptsp"]
            : [],
    }))
}

function isMissingExecutable(error) {
  const values = [error]
  const seen = new Set()
  const messages = []
  while (values.length) {
    const value = values.shift()
    if (!value || seen.has(value)) continue
    seen.add(value)
    if (typeof value === "string") messages.push(value)
    else if (typeof value === "object") {
      if (typeof value.message === "string") messages.push(value.message)
      if (value.cause) values.push(value.cause)
    }
  }
  const message = messages.join(" ").toLowerCase()
  return MISSING_EXECUTABLE.some((part) => message.includes(part))
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function cleanId(value, name) {
  if (
    typeof value !== "string" ||
    value.trim() !== value ||
    value.length < 1 ||
    value.length > 128 ||
    /[\0-\x1f\x7f]/.test(value)
  ) {
    throw new Error(`Invalid ${name}`)
  }
  return value
}

function cleanSize(value, name, maximum) {
  if (!Number.isInteger(value) || value < 1 || value > maximum) {
    throw new Error(`Invalid terminal ${name}`)
  }
  return value
}

function cleanEnv(value) {
  if (value === undefined) return null
  if (!isRecord(value) || Object.keys(value).length > 128) {
    throw new Error("Invalid terminal environment")
  }
  const result = {}
  for (const [key, entry] of Object.entries(value)) {
    if (
      !/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(key) ||
      typeof entry !== "string" ||
      entry.length > 8_192 ||
      shouldExcludeEnv(key)
    ) {
      throw new Error("Invalid terminal environment")
    }
    result[key] = entry
  }
  return result
}

function shouldExcludeEnv(key) {
  const normalized = key.toUpperCase()
  return (
    INTERNAL_ENV.has(normalized) ||
    normalized.startsWith("ELECTRON_") ||
    normalized.startsWith("OPEN_SWE_") ||
    normalized.startsWith("VITE_")
  )
}

function spawnEnv(baseEnv, runtimeEnv, cwd, shell) {
  const result = {}
  for (const [key, value] of Object.entries(baseEnv)) {
    if (typeof value === "string" && !shouldExcludeEnv(key)) result[key] = value
  }
  Object.assign(result, runtimeEnv || {})
  if (result.APPIMAGE !== undefined || result.APPDIR !== undefined) {
    const appDir = result.APPDIR?.replace(/\/+$/, "")
    for (const key of APPIMAGE_ENV) delete result[key]
    if (appDir) {
      for (const key of APPIMAGE_PATH_ENV) {
        if (result[key] === undefined) continue
        const kept = result[key]
          .split(":")
          .filter((entry) => entry && entry !== appDir && !entry.startsWith(`${appDir}/`))
        if (kept.length) result[key] = kept.join(":")
        else delete result[key]
      }
    }
  }
  result.PWD = cwd
  result.SHELL = shell
  return result
}

function containsPath(root, candidate) {
  const relative = path.relative(root, candidate)
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))
}

function terminalLabel(id) {
  const suffix = /^term(?:inal)?-(\d+)$/i.exec(id)?.[1]
  return suffix ? `Terminal ${suffix}` : id
}

function safePart(value) {
  return Buffer.from(value).toString("base64url")
}

function capHistory(value, maxLines = HISTORY_LINES, maxChars = HISTORY_CHARS) {
  let history = value.length > maxChars ? value.slice(-maxChars) : value
  const trailing = history.endsWith("\n")
  const lines = history.split("\n")
  if (trailing) lines.pop()
  if (lines.length > maxLines) history = lines.slice(-maxLines).join("\n") + (trailing ? "\n" : "")
  return history
}

function isCsiFinal(code) {
  return code >= 0x40 && code <= 0x7e
}

function stripCsi(body, finalByte) {
  return (
    finalByte === "n" ||
    (finalByte === "R" && /^[0-9;?]*$/.test(body)) ||
    (finalByte === "c" && /^[>0-9;?]*$/.test(body)) ||
    ((finalByte === "p" || finalByte === "y") && /^[0-9;?]*\$$/.test(body)) ||
    (finalByte === "q" && /^>[0-9;]*$/.test(body)) ||
    (finalByte === "u" && body.startsWith("?"))
  )
}

function stringEnd(input, start) {
  for (let index = start; index < input.length; index += 1) {
    if (input.charCodeAt(index) === 0x07 || input.charCodeAt(index) === 0x9c) return index + 1
    if (input.charCodeAt(index) === 0x1b && input.charCodeAt(index + 1) === 0x5c) return index + 2
  }
  return null
}

function stripTerminator(value) {
  if (value.endsWith("\u001b\\")) return value.slice(0, -2)
  return /[\u0007\u009c]$/.test(value) ? value.slice(0, -1) : value
}

function sanitizeHistoryChunk(pending, data) {
  const input = pending + data
  let visible = ""
  let index = 0
  while (index < input.length) {
    const code = input.charCodeAt(index)
    if (code === 0x1b && index + 1 >= input.length) return { visible, pending: input.slice(index) }
    if (code === 0x1b && input.charCodeAt(index + 1) === 0x5b) {
      let cursor = index + 2
      while (cursor < input.length && !isCsiFinal(input.charCodeAt(cursor))) cursor += 1
      if (cursor >= input.length) return { visible, pending: input.slice(index) }
      const sequence = input.slice(index, cursor + 1)
      if (!stripCsi(input.slice(index + 2, cursor), input[cursor])) visible += sequence
      index = cursor + 1
      continue
    }
    if (code === 0x1b && [0x5d, 0x50, 0x5e, 0x5f].includes(input.charCodeAt(index + 1))) {
      const end = stringEnd(input, index + 2)
      if (end === null) return { visible, pending: input.slice(index) }
      const sequence = input.slice(index, end)
      const content = stripTerminator(input.slice(index + 2, end))
      const remove =
        (input.charCodeAt(index + 1) === 0x5d && /^(10|11|12);(?:\?|rgb:)/.test(content)) ||
        (input.charCodeAt(index + 1) === 0x50 && /^[01]?[$+][qr]/.test(content))
      if (!remove) visible += sequence
      index = end
      continue
    }
    if (code === 0x9b) {
      let cursor = index + 1
      while (cursor < input.length && !isCsiFinal(input.charCodeAt(cursor))) cursor += 1
      if (cursor >= input.length) return { visible, pending: input.slice(index) }
      const sequence = input.slice(index, cursor + 1)
      if (!stripCsi(input.slice(index + 1, cursor), input[cursor])) visible += sequence
      index = cursor + 1
      continue
    }
    visible += input[index]
    index += 1
  }
  return { visible, pending: "" }
}

function defaultInspect(pid, platform = process.platform) {
  return new Promise((resolve) => {
    if (platform === "win32") return resolve(null)
    execFile("ps", ["-eo", "pid=,ppid=,comm="], { timeout: 1_000 }, (error, stdout) => {
      if (error) return resolve(null)
      for (const line of stdout.split(/\r?\n/)) {
        const match = /^\s*(\d+)\s+(\d+)\s+(.+?)\s*$/.exec(line)
        if (match && Number(match[2]) === pid) return resolve(path.basename(match[3]).slice(0, 128))
      }
      resolve(null)
    })
  })
}

function createTerminalManager(options) {
  const adapter = options.pty || require("node-pty")
  const fileSystem = options.fs || fs
  const logsDir = options.logsDir
  const listProjects = options.listProjects
  const getLocalThread = options.getLocalThread || options.getAcpSession
  const baseEnv = options.env || getUserShellEnv()
  const inspect = options.inspect || defaultInspect
  const historyLines = options.historyLines || HISTORY_LINES
  const historyChars = options.historyChars || HISTORY_CHARS
  const killGraceMs = options.killGraceMs ?? KILL_GRACE_MS
  const maxSessions = options.maxSessions || MAX_SESSIONS
  const sessions = new Map()
  const listeners = new Set()
  const metadataListeners = new Set()
  const locks = new Map()
  let shuttingDown = false

  fileSystem.mkdirSync(logsDir, { recursive: true, mode: 0o700 })

  function key(localSessionId, terminalId) {
    return `${localSessionId}\0${terminalId}`
  }

  function historyPath(localSessionId, terminalId) {
    return path.join(logsDir, `terminal_${safePart(localSessionId)}_${safePart(terminalId)}.log`)
  }

  function validateIdentity(input) {
    if (!isRecord(input)) throw new Error("Invalid terminal request")
    const allowed = new Set([
      "localSessionId",
      "terminalId",
      "cwd",
      "cols",
      "rows",
      "env",
      "restartIfNotRunning",
      "deleteHistory",
      "data",
    ])
    if (Object.keys(input).some((field) => !allowed.has(field))) {
      throw new Error("Invalid terminal request")
    }
    const localSessionId = cleanId(input.localSessionId, "local session ID")
    const terminalId = cleanId(input.terminalId, "terminal ID")
    const localSession = getLocalThread(localSessionId)
    if (!localSession || typeof localSession.cwd !== "string") {
      throw new Error("Local thread not found")
    }
    return { localSessionId, terminalId, localSession }
  }

  function validateCwd(value, localSession) {
    if (typeof value !== "string" || !path.isAbsolute(value)) {
      throw new Error("Choose a valid local project directory")
    }
    let cwd
    let sessionCwd
    try {
      cwd = fileSystem.realpathSync(value)
      sessionCwd = fileSystem.realpathSync(localSession.cwd)
      if (!fileSystem.statSync(cwd).isDirectory()) throw new Error("not directory")
    } catch {
      throw new Error("Choose a valid local project directory")
    }
    const roots = listProjects().map((project) => {
      try {
        return fileSystem.realpathSync(project.cwd)
      } catch {
        return null
      }
    })
    if (!containsPath(sessionCwd, cwd) || !roots.some((root) => root && containsPath(root, cwd))) {
      throw new Error("Terminal directory must belong to this registered local session")
    }
    return cwd
  }

  function validateLaunch(input, existing) {
    const identity = validateIdentity(input)
    const cwd = validateCwd(input.cwd ?? existing?.cwd, identity.localSession)
    const env = input.env === undefined ? existing?.runtimeEnv ?? null : cleanEnv(input.env)
    return {
      ...identity,
      cwd,
      cols: input.cols === undefined ? existing?.cols || DEFAULT_COLS : cleanSize(input.cols, "columns", 1_000),
      rows: input.rows === undefined ? existing?.rows || DEFAULT_ROWS : cleanSize(input.rows, "rows", 500),
      env,
    }
  }

  function snapshot(session) {
    return {
      localSessionId: session.localSessionId,
      terminalId: session.terminalId,
      cwd: session.cwd,
      status: session.status,
      pid: session.pid,
      history: session.history,
      exitCode: session.exitCode,
      exitSignal: session.exitSignal,
      hasRunningSubprocess: session.hasRunningSubprocess,
      label: session.childLabel || terminalLabel(session.terminalId),
      updatedAt: session.updatedAt,
      sequence: session.sequence,
    }
  }

  function summary(session) {
    const value = snapshot(session)
    delete value.history
    delete value.sequence
    return value
  }

  function emit(session, type, detail = {}) {
    session.sequence += 1
    session.updatedAt = new Date().toISOString()
    const event = {
      type,
      localSessionId: session.localSessionId,
      terminalId: session.terminalId,
      sequence: session.sequence,
      ...detail,
      ...(detail.withSnapshot ? { snapshot: snapshot(session) } : {}),
    }
    delete event.withSnapshot
    for (const listener of listeners) listener(event)
    if (["started", "restarted", "exited", "error", "activity"].includes(type)) {
      const metadata = { type: "upsert", terminal: summary(session) }
      for (const listener of metadataListeners) listener(metadata)
    } else if (type === "closed") {
      const metadata = {
        type: "remove",
        localSessionId: session.localSessionId,
        terminalId: session.terminalId,
      }
      for (const listener of metadataListeners) listener(metadata)
    }
    return event
  }

  function readHistory(localSessionId, terminalId) {
    const filePath = historyPath(localSessionId, terminalId)
    try {
      const raw = fileSystem.readFileSync(filePath, "utf8")
      const capped = capHistory(raw, historyLines, historyChars)
      if (capped !== raw) atomicWrite(filePath, capped)
      return capped
    } catch {
      return ""
    }
  }

  function atomicWrite(filePath, value) {
    const temporary = `${filePath}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`
    fileSystem.writeFileSync(temporary, value, { mode: 0o600 })
    fileSystem.renameSync(temporary, filePath)
  }

  function persist(session, immediate = false) {
    if (session.persistTimer) clearTimeout(session.persistTimer)
    const write = () => {
      session.persistTimer = null
      try {
        atomicWrite(historyPath(session.localSessionId, session.terminalId), session.history)
      } catch {}
    }
    if (immediate) write()
    else {
      session.persistTimer = setTimeout(write, 40)
      session.persistTimer.unref?.()
    }
  }

  function flush(session) {
    if (!session.persistTimer) return
    clearTimeout(session.persistTimer)
    session.persistTimer = null
    try {
      atomicWrite(historyPath(session.localSessionId, session.terminalId), session.history)
    } catch {}
  }

  function locked(localSessionId, operation) {
    const previous = locks.get(localSessionId) || Promise.resolve()
    const result = previous.catch(() => {}).then(operation)
    locks.set(localSessionId, result)
    return result.finally(() => {
      if (locks.get(localSessionId) === result) locks.delete(localSessionId)
    })
  }

  function stopProcess(session) {
    const processHandle = session.process
    if (!processHandle) return
    session.process = null
    session.acceptingData = false
    session.dataDisposable?.dispose?.()
    session.exitDisposable?.dispose?.()
    session.dataDisposable = null
    session.exitDisposable = null
    session.processEvents = []
    session.pendingResize = null
    try {
      processHandle.kill("SIGTERM")
    } catch {}
    const timer = setTimeout(() => {
      try {
        processHandle.kill("SIGKILL")
      } catch {}
    }, killGraceMs)
    timer.unref?.()
  }

  function updateActivity(session, processHandle) {
    Promise.resolve(inspect(processHandle.pid)).then((label) => {
      if (session.process !== processHandle || session.status !== "running") return
      const childLabel = typeof label === "string" && label.trim() ? label.trim().slice(0, 128) : null
      if (session.childLabel === childLabel) return
      session.childLabel = childLabel
      session.hasRunningSubprocess = childLabel !== null
      emit(session, "activity", {
        hasRunningSubprocess: session.hasRunningSubprocess,
        label: session.childLabel || terminalLabel(session.terminalId),
      })
    }).catch(() => {})
  }

  function startActivityPoll(session, processHandle) {
    clearInterval(session.activityTimer)
    updateActivity(session, processHandle)
    session.activityTimer = setInterval(() => updateActivity(session, processHandle), 1_000)
    session.activityTimer.unref?.()
  }

  function enqueueProcessEvent(session, processHandle, event) {
    if (session.process !== processHandle || session.status !== "running") return
    session.processEvents.push(event)
    if (session.processEventDrain) return
    session.processEventDrain = Promise.resolve().then(() => {
      while (session.process === processHandle && session.status === "running") {
        const next = session.processEvents.shift()
        if (!next) break
        if (next.type === "output") {
          const sanitized = sanitizeHistoryChunk(session.pendingControl, next.data)
          session.pendingControl = sanitized.pending
          session.history = capHistory(session.history + sanitized.visible, historyLines, historyChars)
          persist(session)
          emit(session, "output", { data: next.data })
          continue
        }
        session.acceptingData = false
        session.process = null
        session.pid = null
        session.status = "exited"
        session.exitCode = Number.isInteger(next.exit?.exitCode) ? next.exit.exitCode : null
        session.exitSignal = Number.isInteger(next.exit?.signal) ? next.exit.signal : null
        session.processEvents = []
        clearInterval(session.activityTimer)
        session.activityTimer = null
        flush(session)
        emit(session, "exited", {
          exitCode: session.exitCode,
          exitSignal: session.exitSignal,
        })
        evictInactive()
        break
      }
    }).finally(() => {
      session.processEventDrain = null
      if (session.process === processHandle && session.status === "running" && session.processEvents.length) {
        const next = session.processEvents.shift()
        enqueueProcessEvent(session, processHandle, next)
      }
    })
  }

  function spawn(session, eventType) {
    if (shuttingDown) throw new Error("Terminal manager is shutting down")
    session.status = "starting"
    session.exitCode = null
    session.exitSignal = null
    session.childLabel = null
    session.hasRunningSubprocess = false
    let lastError
    for (const candidate of shellCandidates(baseEnv, options.platform)) {
      try {
        const processHandle = adapter.spawn(candidate.shell, candidate.args, {
          name: "xterm-256color",
          cols: session.cols,
          rows: session.rows,
          cwd: session.cwd,
          env: spawnEnv(baseEnv, session.runtimeEnv, session.cwd, candidate.shell),
        })
        session.process = processHandle
        session.pid = Number.isInteger(processHandle.pid) && processHandle.pid > 0 ? processHandle.pid : null
        session.status = "running"
        session.acceptingData = true
        session.dataDisposable = processHandle.onData((data) => {
          if (!session.acceptingData || typeof data !== "string") return
          enqueueProcessEvent(session, processHandle, { type: "output", data })
        })
        session.exitDisposable = processHandle.onExit((exit) => {
          enqueueProcessEvent(session, processHandle, { type: "exit", exit })
        })
        startActivityPoll(session, processHandle)
        emit(session, eventType, { withSnapshot: true })
        return snapshot(session)
      } catch (error) {
        lastError = error
        if (!isMissingExecutable(error)) break
      }
    }
    session.status = "error"
    session.pid = null
    const message = lastError instanceof Error ? lastError.message : "Terminal failed to start"
    emit(session, "error", { message })
    throw new Error(message)
  }

  function newSession(input) {
    return {
      localSessionId: input.localSessionId,
      terminalId: input.terminalId,
      cwd: input.cwd,
      cols: input.cols,
      rows: input.rows,
      runtimeEnv: input.env,
      status: "starting",
      pid: null,
      history: readHistory(input.localSessionId, input.terminalId),
      pendingControl: "",
      exitCode: null,
      exitSignal: null,
      childLabel: null,
      hasRunningSubprocess: false,
      sequence: 0,
      updatedAt: new Date().toISOString(),
      process: null,
      acceptingData: false,
      dataDisposable: null,
      exitDisposable: null,
      processEvents: [],
      processEventDrain: null,
      pendingResize: null,
      resizePromise: null,
      persistTimer: null,
      activityTimer: null,
    }
  }

  function evictInactive() {
    const inactive = [...sessions.values()]
      .filter((session) => session.status === "exited" || session.status === "error")
      .sort((left, right) =>
        left.updatedAt.localeCompare(right.updatedAt) ||
        left.localSessionId.localeCompare(right.localSessionId) ||
        left.terminalId.localeCompare(right.terminalId)
      )
    while (inactive.length > maxSessions) {
      const session = inactive.shift()
      sessions.delete(key(session.localSessionId, session.terminalId))
      clearInterval(session.activityTimer)
      flush(session)
    }
  }

  async function open(input, eventType = "started") {
    const identity = validateIdentity(input)
    return locked(identity.localSessionId, () => {
      const sessionKey = key(identity.localSessionId, identity.terminalId)
      const existing = sessions.get(sessionKey)
      const launch = validateLaunch(input, existing)
      if (existing?.process && existing.cwd === launch.cwd && JSON.stringify(existing.runtimeEnv) === JSON.stringify(launch.env)) {
        if (existing.cols !== launch.cols || existing.rows !== launch.rows) {
          existing.process.resize(launch.cols, launch.rows)
          existing.cols = launch.cols
          existing.rows = launch.rows
          existing.updatedAt = new Date().toISOString()
        }
        return snapshot(existing)
      }
      if (existing) {
        stopProcess(existing)
        existing.cwd = launch.cwd
        existing.cols = launch.cols
        existing.rows = launch.rows
        existing.runtimeEnv = launch.env
        existing.history = ""
        existing.pendingControl = ""
        persist(existing, true)
        return spawn(existing, eventType)
      }
      const session = newSession(launch)
      sessions.set(sessionKey, session)
      evictInactive()
      return spawn(session, eventType)
    })
  }

  async function attach(input, listener) {
    const identity = validateIdentity(input)
    const sessionKey = key(identity.localSessionId, identity.terminalId)
    const buffered = []
    let live = false
    const subscription = (event) => {
      if (event.localSessionId !== identity.localSessionId || event.terminalId !== identity.terminalId) return
      if (live) listener(event)
      else buffered.push(event)
    }
    listeners.add(subscription)
    try {
      let current = sessions.get(sessionKey)
      if (!current) {
        if (input.cwd === undefined) throw new Error("Terminal session not found")
        await open(input)
        current = sessions.get(sessionKey)
      } else if (!current.process && input.restartIfNotRunning === true) {
        await open({ ...input, cwd: input.cwd ?? current.cwd })
        current = sessions.get(sessionKey)
      } else if (input.cols !== undefined || input.rows !== undefined) {
        await resize({
          localSessionId: identity.localSessionId,
          terminalId: identity.terminalId,
          cols: input.cols ?? current.cols,
          rows: input.rows ?? current.rows,
        })
      }
      const initial = snapshot(current)
      for (const event of buffered) {
        if (event.sequence > initial.sequence) listener(event)
      }
      live = true
      return { snapshot: initial, detach: () => listeners.delete(subscription) }
    } catch (error) {
      listeners.delete(subscription)
      throw error
    }
  }

  async function write(input) {
    const identity = validateIdentity(input)
    if (typeof input.data !== "string" || input.data.length < 1 || input.data.length > 65_536) {
      throw new Error("Invalid terminal input")
    }
    const session = sessions.get(key(identity.localSessionId, identity.terminalId))
    if (!session?.process || session.status !== "running") return
    session.process.write(input.data)
  }

  async function resize(input) {
    const identity = validateIdentity(input)
    const request = {
      cols: cleanSize(input.cols, "columns", 1_000),
      rows: cleanSize(input.rows, "rows", 500),
    }
    const session = sessions.get(key(identity.localSessionId, identity.terminalId))
    if (!session) return
    session.pendingResize = request
    if (session.resizePromise) return session.resizePromise
    const result = locked(identity.localSessionId, () => {
      while (session.pendingResize) {
        const latest = session.pendingResize
        session.pendingResize = null
        const current = sessions.get(key(identity.localSessionId, identity.terminalId))
        if (current !== session || !session.process || session.status !== "running") return
        session.process.resize(latest.cols, latest.rows)
        session.cols = latest.cols
        session.rows = latest.rows
        session.updatedAt = new Date().toISOString()
      }
    })
    session.resizePromise = result
    return result.finally(() => {
      if (session.resizePromise === result) session.resizePromise = null
      if (session.pendingResize) void resize({ ...input, ...session.pendingResize })
    })
  }

  async function clear(input) {
    const identity = validateIdentity(input)
    return locked(identity.localSessionId, () => {
      const session = sessions.get(key(identity.localSessionId, identity.terminalId))
      if (!session) throw new Error("Terminal session not found")
      session.history = ""
      session.pendingControl = ""
      persist(session, true)
      emit(session, "cleared")
    })
  }

  async function restart(input) {
    const identity = validateIdentity(input)
    const existing = sessions.get(key(identity.localSessionId, identity.terminalId))
    return open({
      ...input,
      cwd: input.cwd ?? existing?.cwd,
      cols: input.cols ?? existing?.cols ?? DEFAULT_COLS,
      rows: input.rows ?? existing?.rows ?? DEFAULT_ROWS,
    }, "restarted")
  }

  async function close(input) {
    if (!isRecord(input)) throw new Error("Invalid terminal request")
    const localSessionId = cleanId(input.localSessionId, "local session ID")
    if (!getLocalThread(localSessionId)) throw new Error("Local thread not found")
    if (input.terminalId !== undefined) {
      const terminalId = cleanId(input.terminalId, "terminal ID")
      return locked(localSessionId, () => closeOne(localSessionId, terminalId, input.deleteHistory === true))
    }
    return locked(localSessionId, () => {
      for (const session of [...sessions.values()]) {
        if (session.localSessionId === localSessionId) {
          closeOne(localSessionId, session.terminalId, input.deleteHistory === true)
        }
      }
    })
  }

  function closeOne(localSessionId, terminalId, deleteHistory) {
    const session = sessions.get(key(localSessionId, terminalId))
    if (!session) return
    sessions.delete(key(localSessionId, terminalId))
    clearInterval(session.activityTimer)
    flush(session)
    stopProcess(session)
    if (deleteHistory) {
      try {
        fileSystem.rmSync(historyPath(localSessionId, terminalId), { force: true })
      } catch {}
    }
    emit(session, "closed")
  }

  function list(localSessionId) {
    cleanId(localSessionId, "local session ID")
    if (!getLocalThread(localSessionId)) throw new Error("Local thread not found")
    return [...sessions.values()]
      .filter((session) => session.localSessionId === localSessionId)
      .map(summary)
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt) || left.terminalId.localeCompare(right.terminalId))
  }

  function subscribeMetadata(localSessionId, listener) {
    cleanId(localSessionId, "local session ID")
    if (!getLocalThread(localSessionId)) throw new Error("Local thread not found")
    const subscription = (event) => {
      const eventSessionId = event.terminal?.localSessionId ?? event.localSessionId
      if (eventSessionId === localSessionId) listener(event)
    }
    metadataListeners.add(subscription)
    return { terminals: list(localSessionId), detach: () => metadataListeners.delete(subscription) }
  }

  async function shutdown() {
    if (shuttingDown) return
    shuttingDown = true
    const processes = []
    for (const session of sessions.values()) {
      clearInterval(session.activityTimer)
      flush(session)
      if (session.process) processes.push(session.process)
      session.dataDisposable?.dispose?.()
      session.exitDisposable?.dispose?.()
      try {
        session.process?.kill("SIGTERM")
      } catch {}
      session.process = null
    }
    await new Promise((resolve) => setTimeout(resolve, killGraceMs))
    for (const processHandle of processes) {
      try {
        processHandle.kill("SIGKILL")
      } catch {}
    }
    sessions.clear()
    listeners.clear()
    metadataListeners.clear()
  }

  return {
    attach,
    clear,
    close,
    list,
    open,
    resize,
    restart,
    shutdown,
    subscribeMetadata,
    write,
  }
}

function configureTerminalIpc({
  ipcMain,
  requireTrusted,
  getWindow,
  listProjects,
  getLocalThread,
  userDataPath,
}) {
  ensurePtySpawnHelperExecutable()
  configuredManager = createTerminalManager({
    logsDir: path.join(userDataPath, "terminal-history"),
    listProjects,
    getLocalThread,
  })
  const attachments = new Map()
  const metadataAttachments = new Map()

  function senderKey(event, input) {
    return `${event.sender.id}\0${input.localSessionId}\0${input.terminalId}`
  }

  function handle(channel, operation) {
    ipcMain.handle(channel, async (event, input) => {
      requireTrusted(event)
      return operation(event, input)
    })
  }

  handle("desktop:terminal-attach", async (event, input) => {
    const attachmentKey = senderKey(event, input)
    attachments.get(attachmentKey)?.()
    const attached = await configuredManager.attach(input, (terminalEvent) => {
      if (!event.sender.isDestroyed()) event.sender.send("desktop:terminal-event", terminalEvent)
    })
    attachments.set(attachmentKey, attached.detach)
    return attached.snapshot
  })
  handle("desktop:terminal-write", (_event, input) => configuredManager.write(input))
  handle("desktop:terminal-resize", (_event, input) => configuredManager.resize(input))
  handle("desktop:terminal-clear", (_event, input) => configuredManager.clear(input))
  handle("desktop:terminal-restart", (_event, input) => configuredManager.restart(input))
  handle("desktop:terminal-detach", (event, input) => {
    const attachmentKey = senderKey(event, input)
    attachments.get(attachmentKey)?.()
    attachments.delete(attachmentKey)
  })
  handle("desktop:terminal-close", (_event, input) => configuredManager.close(input))
  handle("desktop:terminal-list", (_event, localSessionId) => configuredManager.list(localSessionId))
  handle("desktop:terminal-metadata-subscribe", (event, localSessionId) => {
    const subscriptionKey = `${event.sender.id}\0${localSessionId}`
    metadataAttachments.get(subscriptionKey)?.()
    const subscribed = configuredManager.subscribeMetadata(localSessionId, (metadataEvent) => {
      if (!event.sender.isDestroyed()) event.sender.send("desktop:terminal-metadata", metadataEvent)
    })
    metadataAttachments.set(subscriptionKey, subscribed.detach)
    return subscribed.terminals
  })
  handle("desktop:terminal-metadata-detach", (event, localSessionId) => {
    const subscriptionKey = `${event.sender.id}\0${localSessionId}`
    metadataAttachments.get(subscriptionKey)?.()
    metadataAttachments.delete(subscriptionKey)
  })

  getWindow()?.webContents.once("destroyed", () => {
    for (const detach of attachments.values()) detach()
    for (const detach of metadataAttachments.values()) detach()
    attachments.clear()
    metadataAttachments.clear()
  })
  return configuredManager
}

function closeAllTerminals() {
  return configuredManager?.shutdown() || Promise.resolve()
}

module.exports = {
  capHistory,
  closeAllTerminals,
  configureTerminalIpc,
  createTerminalManager,
  ensurePtySpawnHelperExecutable,
  sanitizeHistoryChunk,
}
