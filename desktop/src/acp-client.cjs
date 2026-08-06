const { spawn } = require("node:child_process")
const fs = require("node:fs")
const path = require("node:path")
const readline = require("node:readline")
const { randomUUID } = require("node:crypto")

const ACP_PROTOCOL_VERSION = 1

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function dcodeTarget({
  env = process.env,
  platform = process.platform,
  modelId,
  effort,
} = {}) {
  const args = ["--acp"]
  if (modelId) args.push("--model", modelId)
  if (effort) {
    args.push("--model-params", JSON.stringify({ reasoning_effort: effort }))
  }
  if (env.OPEN_SWE_DCODE_COMMAND) {
    return { command: env.OPEN_SWE_DCODE_COMMAND, args }
  }
  const home = env.HOME || env.USERPROFILE
  const installedCommand = home
    ? path.join(
        home,
        ".local",
        "bin",
        platform === "win32" ? "dcode.exe" : "dcode"
      )
    : null
  if (installedCommand && fs.existsSync(installedCommand)) {
    return { command: installedCommand, args }
  }
  return { command: "dcode", args }
}

function sessionTitle(text) {
  const value = text.trim().replace(/\s+/g, " ")
  return value.slice(0, 80) || "New local agent"
}

function promptBlocks(text, images = []) {
  return [
    ...(text.trim() ? [{ type: "text", text: text.trim() }] : []),
    ...images.map((image) => ({
      type: "image",
      data: image.base64,
      mimeType: image.mimeType,
    })),
  ]
}

function contentText(content) {
  if (!Array.isArray(content)) return ""
  return content
    .map((item) => {
      if (!isRecord(item)) return ""
      if (item.type === "content" && isRecord(item.content)) {
        return item.content.type === "text" &&
          typeof item.content.text === "string"
          ? item.content.text
          : ""
      }
      if (item.type === "diff" && typeof item.path === "string") {
        return `Updated ${item.path}`
      }
      return ""
    })
    .filter(Boolean)
    .join("\n")
}

function normalizeTool(update, previous = {}) {
  const rawInput = isRecord(update.rawInput)
    ? update.rawInput
    : previous.input || {}
  const status =
    update.status === "failed"
      ? "error"
      : ["pending", "in_progress", "completed", "error"].includes(update.status)
        ? update.status
        : previous.status || "in_progress"
  return {
    toolCallId: update.toolCallId,
    title:
      typeof update.title === "string"
        ? update.title
        : previous.title || "Tool",
    toolKind:
      typeof update.kind === "string"
        ? update.kind
        : previous.toolKind || "other",
    input: rawInput,
    status,
    output:
      contentText(update.content) ||
      (typeof update.rawOutput === "string"
        ? update.rawOutput
        : previous.output),
    locations: Array.isArray(update.locations)
      ? update.locations
      : previous.locations,
  }
}

class NdJsonRpcClient {
  constructor(command, args, cwd, env) {
    this.process = spawn(command, args, {
      cwd,
      env: { ...env, PWD: cwd, PYTHONUNBUFFERED: "1" },
      stdio: ["pipe", "pipe", "pipe"],
    })
    this.pending = new Map()
    this.nextId = 1
    this.closed = false
    this.onNotification = null
    this.onRequest = null
    this.onFailure = null
    this.stderr = ""

    this.process.stderr.on("data", (chunk) => {
      const text = chunk.toString("utf8")
      this.stderr = `${this.stderr}${text}`.slice(-8_000)
    })
    this.lines = readline.createInterface({ input: this.process.stdout })
    this.lines.on("line", (line) => this.handleLine(line))
    this.process.on("error", (error) => this.fail(error))
    this.process.on("exit", (code, signal) => {
      if (this.closed) return
      const reason = signal ? `signal ${signal}` : `exit code ${code}`
      const detail = this.stderr.trim()
      this.fail(
        new Error(
          `Deep Agents Code stopped with ${reason}${detail ? `: ${detail}` : ""}`
        )
      )
    })
  }

  request(method, params) {
    if (this.closed)
      return Promise.reject(new Error("Deep Agents Code is not running"))
    const id = this.nextId++
    this.write({ jsonrpc: "2.0", id, method, params })
    return new Promise((resolve, reject) =>
      this.pending.set(id, { method, resolve, reject })
    )
  }

  notify(method, params) {
    if (!this.closed) this.write({ jsonrpc: "2.0", method, params })
  }

  write(message) {
    this.process.stdin.write(`${JSON.stringify(message)}\n`)
  }

  respond(id, result) {
    this.write({ jsonrpc: "2.0", id, result })
  }

  respondError(id, message) {
    this.write({ jsonrpc: "2.0", id, error: { code: -32000, message } })
  }

  handleLine(line) {
    let message
    try {
      message = JSON.parse(line)
    } catch {
      return
    }
    if (!isRecord(message)) return

    const hasId =
      typeof message.id === "number" || typeof message.id === "string"
    if (hasId && ("result" in message || "error" in message)) {
      const pending = this.pending.get(message.id)
      if (!pending) return
      this.pending.delete(message.id)
      if ("error" in message) {
        const detail = isRecord(message.error) && message.error.message
        pending.reject(
          new Error(`[acp:${pending.method}] ${detail || "Request failed"}`)
        )
      } else {
        pending.resolve(message.result)
      }
      return
    }

    if (typeof message.method !== "string") return
    const params = "params" in message ? message.params : {}
    if (!hasId) {
      this.onNotification?.(message.method, params)
      return
    }
    Promise.resolve(this.onRequest?.(message.method, params) ?? {})
      .then((result) =>
        this.respond(message.id, isRecord(result) ? result : {})
      )
      .catch((error) =>
        this.respondError(message.id, String(error?.message || error))
      )
  }

  fail(error) {
    if (this.closed) return
    this.closed = true
    this.onFailure?.(error)
    this.rejectPending(error)
  }

  rejectPending(error) {
    const pending = [...this.pending.values()]
    this.pending.clear()
    for (const request of pending) request.reject(error)
  }

  close() {
    if (this.closed) return
    this.closed = true
    this.lines.close()
    this.process.stdin.end()
    if (!this.process.killed) this.process.kill()
    this.rejectPending(new Error("Deep Agents Code session closed"))
  }
}

class AcpSession {
  constructor({ cwd, target, env, onEvent, requestPermission }) {
    this.id = randomUUID()
    this.cwd = cwd
    this.title = "New local agent"
    this.createdAt = Date.now()
    this.updatedAt = this.createdAt
    this.status = "starting"
    this.events = []
    this.onEvent = onEvent
    this.requestPermission = requestPermission
    this.tools = new Map()
    this.rpc = new NdJsonRpcClient(target.command, target.args, cwd, env)
    this.rpc.onNotification = (method, params) =>
      this.handleNotification(method, params)
    this.rpc.onRequest = (method, params) => this.handleRequest(method, params)
    this.rpc.onFailure = (error) => {
      if (this.status === "error") return
      this.status = "error"
      this.emit({ type: "error", message: error.message })
    }
  }

  emit(event) {
    if (event.type === "user-message" && this.title === "New local agent") {
      this.title = sessionTitle(event.text || "")
    }
    this.updatedAt = Date.now()
    const stamped = {
      ...event,
      sequence: this.events.length,
      timestamp: new Date().toISOString(),
    }
    this.events.push(stamped)
    this.onEvent(this.id, stamped)
  }

  async initialize() {
    await this.rpc.request("initialize", {
      protocolVersion: ACP_PROTOCOL_VERSION,
      clientCapabilities: {
        fs: { readTextFile: false, writeTextFile: false },
        terminal: false,
      },
      clientInfo: {
        name: "open-swe-desktop",
        title: "Open SWE Desktop",
        version: "0.1.0",
      },
    })
    const result = await this.rpc.request("session/new", {
      cwd: this.cwd,
      mcpServers: [],
    })
    if (!isRecord(result) || typeof result.sessionId !== "string") {
      throw new Error("Deep Agents Code did not create an ACP session")
    }
    this.acpSessionId = result.sessionId
    this.status = "idle"
  }

  async prompt(text, images) {
    if (this.status === "running")
      throw new Error("Deep Agents Code is already running")
    this.status = "running"
    this.emit({ type: "user-message", text, images })
    this.emit({ type: "run-start" })
    try {
      await this.rpc.request("session/prompt", {
        sessionId: this.acpSessionId,
        prompt: promptBlocks(text, images),
      })
      this.status = "idle"
      this.emit({ type: "run-end" })
    } catch (error) {
      if (this.status !== "error") {
        this.status = "error"
        this.emit({ type: "error", message: String(error?.message || error) })
      }
      throw error
    }
  }

  cancel() {
    if (this.acpSessionId) {
      this.rpc.notify("session/cancel", { sessionId: this.acpSessionId })
    }
  }

  handleNotification(method, params) {
    if (
      method !== "session/update" ||
      !isRecord(params) ||
      !isRecord(params.update)
    )
      return
    const update = params.update
    if (
      update.sessionUpdate === "agent_message_chunk" &&
      isRecord(update.content)
    ) {
      if (
        update.content.type === "text" &&
        typeof update.content.text === "string"
      ) {
        this.emit({ type: "agent-text", text: update.content.text })
      }
      return
    }
    if (
      update.sessionUpdate === "agent_thought_chunk" &&
      isRecord(update.content)
    ) {
      if (
        update.content.type === "text" &&
        typeof update.content.text === "string"
      ) {
        this.emit({ type: "agent-reasoning", text: update.content.text })
      }
      return
    }
    if (["tool_call", "tool_call_update"].includes(update.sessionUpdate)) {
      if (typeof update.toolCallId !== "string") return
      const tool = normalizeTool(update, this.tools.get(update.toolCallId))
      this.tools.set(update.toolCallId, tool)
      this.emit({ type: "tool", tool })
    }
  }

  async handleRequest(method, params) {
    if (method !== "session/request_permission" || !isRecord(params)) return {}
    const options = Array.isArray(params.options)
      ? params.options.filter(isRecord)
      : []
    const allow = await this.requestPermission(params)
    const kinds = allow
      ? ["allow_once", "allow_always"]
      : ["reject_once", "reject_always"]
    const selected = kinds
      .map((kind) => options.find((option) => option.kind === kind))
      .find(Boolean)
    return selected && typeof selected.optionId === "string"
      ? { outcome: { outcome: "selected", optionId: selected.optionId } }
      : { outcome: { outcome: "cancelled" } }
  }

  snapshot() {
    return {
      ...this.summary(),
      events: this.events,
    }
  }

  summary() {
    return {
      id: this.id,
      cwd: this.cwd,
      title: this.title,
      status: this.status,
      createdAt: this.createdAt,
      updatedAt: this.updatedAt,
    }
  }

  close() {
    this.rpc.close()
  }
}

module.exports = {
  AcpSession,
  dcodeTarget,
  promptBlocks,
  sessionTitle,
}
