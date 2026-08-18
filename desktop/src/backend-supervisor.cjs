const { spawn: spawnProcess } = require("node:child_process")
const { randomBytes } = require("node:crypto")
const fs = require("node:fs")
const http = require("node:http")
const path = require("node:path")

const HOST = "127.0.0.1"
const START_TIMEOUT_MS = 60_000
const STOP_TIMEOUT_MS = 5_000
const PROVIDER_KEYS = {
  anthropic: ["ANTHROPIC_API_KEY"],
  fireworks: ["FIREWORKS_API_KEY"],
  google_genai: ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
  openai: ["OPENAI_API_KEY"],
}

function devBackendTarget({ repoRoot, port, env = process.env }) {
  return {
    command: env.OPEN_SWE_LOCAL_BACKEND_COMMAND || env.OPEN_SWE_UV_COMMAND || "uv",
    args: [
      "run",
      "langgraph",
      "dev",
      "--no-browser",
      "--no-reload",
      "--host",
      HOST,
      "--port",
      String(port),
      "--config",
      path.join(repoRoot, "langgraph.desktop.json"),
    ],
    cwd: repoRoot,
  }
}

function packagedBackendTarget({
  resourcesPath,
  port,
  stateDir,
  platform = process.platform,
}) {
  const root = path.join(resourcesPath, "local-backend")
  const executable = path.join(
    root,
    "runtime",
    platform === "win32" ? "python.exe" : "bin/python3"
  )
  return {
    command: executable,
    args: [
      "-m",
      "langgraph_cli",
      "dev",
      "--no-browser",
      "--no-reload",
      "--host",
      HOST,
      "--port",
      String(port),
      "--config",
      path.join(root, "langgraph.json"),
    ],
    cwd: stateDir || root,
  }
}

function localBackendTarget(options) {
  return options.isPackaged ? packagedBackendTarget(options) : devBackendTarget(options)
}

function reservePort(host = HOST) {
  return new Promise((resolve, reject) => {
    const server = http.createServer()
    server.unref()
    server.once("error", reject)
    server.listen(0, host, () => {
      const address = server.address()
      const port = typeof address === "object" && address ? address.port : null
      server.close((error) => (error || !port ? reject(error || new Error("No port")) : resolve(port)))
    })
  })
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

function modelCredentialStatus(modelId, env) {
  const provider = typeof modelId === "string" ? modelId.split(":", 1)[0] : ""
  const variables = PROVIDER_KEYS[provider]
  if (!variables) return { available: true, variable: null }
  const variable = variables.find((key) => env[key])
  return { available: Boolean(variable), variable: variable || variables[0] }
}

class BackendSupervisor {
  constructor(options) {
    this.options = options
    this.spawn = options.spawn || spawnProcess
    this.fetch = options.fetch || fetch
    this.reservePort = options.reservePort || reservePort
    this.child = null
    this.port = null
    this.token = null
    this.logs = ""
    this.closing = false
    this.ready = null
    this.failure = null
  }

  start() {
    if (this.ready && this.child && !this.failure) return this.ready
    this.ready = this.startOnce().catch((error) => {
      this.ready = null
      throw error
    })
    return this.ready
  }

  async startOnce() {
    this.closing = false
    this.failure = null
    this.logs = ""
    this.port = await this.reservePort(HOST)
    this.token = randomBytes(32).toString("base64url")
    const target = localBackendTarget({ ...this.options, port: this.port })
    if (!this.options.projectsFile) throw new Error("Local project allowlist is not configured")
    if (this.options.stateDir) fs.mkdirSync(this.options.stateDir, { recursive: true })
    if (this.options.isPackaged && !fs.existsSync(target.command)) {
      throw new Error(`Bundled local backend is missing: ${target.command}`)
    }
    const child = this.spawn(target.command, target.args, {
      cwd: target.cwd,
      env: {
        ...process.env,
        ...this.options.env,
        OPEN_SWE_LOCAL_AUTH_TOKEN: this.token,
        OPEN_SWE_LOCAL_PROJECTS_FILE: this.options.projectsFile,
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    })
    this.child = child
    const append = (chunk) => {
      this.logs = `${this.logs}${chunk.toString("utf8")}`.slice(-16_000)
    }
    child.stdout?.on("data", append)
    child.stderr?.on("data", append)

    let startupError = null
    const exited = new Promise((resolve) => {
      child.once("error", (error) => {
        startupError = error
        if (!this.closing) this.failure = error
        resolve()
      })
      child.once("exit", (code, signal) => {
        if (!startupError) {
          const reason = signal ? `signal ${signal}` : `exit code ${code}`
          startupError = new Error(`Local LangGraph backend stopped with ${reason}`)
        }
        if (!this.closing) this.failure = startupError
        resolve()
      })
    })
    const deadline = Date.now() + (this.options.startTimeoutMs || START_TIMEOUT_MS)
    while (Date.now() < deadline) {
      if (startupError) break
      try {
        const response = await this.fetch(`http://${HOST}:${this.port}/`, {
          headers: { authorization: `Bearer ${this.token}` },
          signal: AbortSignal.timeout(1_000),
        })
        if (response.ok) {
          this.failure = null
          return this.publicConfig()
        }
      } catch {}
      await Promise.race([delay(150), exited])
    }
    await this.close()
    const detail = this.logs.trim()
    if (startupError) {
      throw new Error(`${startupError.message}${detail ? `\n${detail}` : ""}`)
    }
    throw new Error(`Local LangGraph backend did not become healthy${detail ? `\n${detail}` : ""}`)
  }

  credentialStatus(modelId) {
    return modelCredentialStatus(modelId, { ...process.env, ...this.options.env })
  }

  publicConfig() {
    return { apiUrl: "/local-graph", graphId: "agent" }
  }

  async request(pathname, init = {}) {
    await this.start()
    const headers = new Headers(init.headers)
    headers.set("authorization", `Bearer ${this.token}`)
    headers.set("accept-encoding", "identity")
    return this.fetch(`http://${HOST}:${this.port}${pathname}`, { ...init, headers })
  }

  async createThread(threadId) {
    const response = await this.request("/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        if_exists: "do_nothing",
        metadata: { graph_id: "agent" },
      }),
    })
    if (!response.ok) {
      throw new Error(`Could not create local LangGraph thread (${response.status})`)
    }
  }

  async deleteThread(threadId) {
    const response = await this.request(`/threads/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
    })
    if (!response.ok && response.status !== 404) {
      throw new Error(`Could not delete local LangGraph thread (${response.status})`)
    }
  }

  async proxy(request, prefix = "/local-graph") {
    const source = new URL(request.url)
    if (source.pathname !== prefix && !source.pathname.startsWith(`${prefix}/`)) {
      return new Response("Not found", { status: 404 })
    }
    const headers = new Headers(request.headers)
    headers.delete("host")
    headers.delete("cookie")
    const body = ["GET", "HEAD"].includes(request.method) ? undefined : request.body
    return this.request(`${source.pathname.slice(prefix.length) || "/"}${source.search}`, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      ...(body ? { duplex: "half" } : {}),
    })
  }

  async close() {
    if (this.closing) return
    this.closing = true
    const child = this.child
    this.child = null
    this.port = null
    this.token = null
    this.ready = null
    this.failure = null
    if (!child || child.exitCode !== null || child.signalCode !== null) return
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        try {
          child.kill("SIGKILL")
        } catch {}
        resolve()
      }, this.options.stopTimeoutMs || STOP_TIMEOUT_MS)
      timer.unref?.()
      child.once("exit", () => {
        clearTimeout(timer)
        resolve()
      })
      try {
        child.kill("SIGTERM")
      } catch {
        clearTimeout(timer)
        resolve()
      }
    })
  }
}

module.exports = {
  BackendSupervisor,
  devBackendTarget,
  localBackendTarget,
  modelCredentialStatus,
  packagedBackendTarget,
  reservePort,
}
