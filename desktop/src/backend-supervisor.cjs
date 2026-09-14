const {
  execFileSync: execFileSyncProcess,
  spawn: spawnProcess,
} = require("node:child_process");
const { randomBytes } = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const HOST = "127.0.0.1";
// `langgraph dev` runs one job per worker unless told otherwise; every thread
// has its own worktree, so runs no longer have to wait for each other.
const JOBS_PER_WORKER = "10";
const START_TIMEOUT_MS = 60_000;
const STOP_TIMEOUT_MS = 5_000;
// Records the backend's pid under the app's own state directory, so a backend
// left behind by a crash or relaunch is reaped before a new one starts and no
// other installation's backend is ever touched.
const PID_FILE = "backend.pid";
const THREAD_STATUS = { busy: "running", error: "error" };
const PROVIDER_KEYS = {
  anthropic: ["ANTHROPIC_API_KEY"],
  fireworks: ["FIREWORKS_API_KEY"],
  google_genai: ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
  openai: ["OPENAI_API_KEY"],
};
const GATEWAY_KEYS = ["LANGSMITH_GATEWAY_API_KEY", "LANGSMITH_API_KEY"];

function devBackendTarget({ repoRoot, port, stateDir, env = process.env }) {
  const config = env.OPEN_SWE_LOCAL_BACKEND_CONFIG || "langgraph.desktop.json";
  return {
    command:
      env.OPEN_SWE_LOCAL_BACKEND_COMMAND || env.OPEN_SWE_UV_COMMAND || "uv",
    args: [
      "run",
      ...(stateDir ? ["--project", repoRoot] : []),
      "langgraph",
      "dev",
      "--no-browser",
      "--no-reload",
      "--host",
      HOST,
      "--port",
      String(port),
      "--n-jobs-per-worker",
      JOBS_PER_WORKER,
      "--config",
      path.resolve(repoRoot, config),
    ],
    cwd: stateDir || repoRoot,
  };
}

function packagedBackendTarget({
  resourcesPath,
  port,
  stateDir,
  platform = process.platform,
}) {
  const root = path.join(resourcesPath, "local-backend");
  const executable = path.join(
    root,
    "runtime",
    platform === "win32" ? "python.exe" : "bin/python3",
  );
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
      "--n-jobs-per-worker",
      JOBS_PER_WORKER,
      "--config",
      path.join(root, "langgraph.json"),
    ],
    cwd: stateDir || root,
  };
}

function localBackendTarget(options) {
  return options.isPackaged
    ? packagedBackendTarget(options)
    : devBackendTarget(options);
}

function reservePort(host = HOST) {
  return new Promise((resolve, reject) => {
    const server = http.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, host, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : null;
      server.close((error) =>
        error || !port ? reject(error || new Error("No port")) : resolve(port),
      );
    });
  });
}

/** `uv run` wraps the python server, so signal the whole process group. */
function killProcessTree(pid, signal) {
  if (process.platform === "win32") {
    execFileSyncProcess("taskkill", ["/pid", String(pid), "/T", "/F"], {
      stdio: "ignore",
    });
    return;
  }
  try {
    process.kill(-pid, signal);
  } catch {
    process.kill(pid, signal);
  }
}

function terminate(child, signal) {
  if (typeof child.pid === "number") {
    try {
      killProcessTree(child.pid, signal);
      return;
    } catch {}
  }
  child.kill(signal);
}

function isLangGraphProcess(pid) {
  if (process.platform === "win32") return true;
  try {
    return /langgraph/.test(
      execFileSyncProcess("ps", ["-o", "command=", "-p", String(pid)], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      }),
    );
  } catch {
    return false;
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function modelCredentialStatus(modelId, env, options = {}) {
  const provider = typeof modelId === "string" ? modelId.split(":", 1)[0] : "";
  const variables = PROVIDER_KEYS[provider];
  if (!variables) return { available: true, variable: null };
  const variable = variables.find((key) => env[key]);
  const gatewayAvailable =
    ["1", "true", "yes", "on"].includes(
      String(env.LANGSMITH_GATEWAY_ENABLED || "")
        .trim()
        .toLowerCase(),
    ) && GATEWAY_KEYS.some((key) => env[key]);
  const oauthAvailable = provider === "openai" && options.openAiOAuth === true;
  return {
    available: Boolean(variable) || gatewayAvailable || oauthAvailable,
    variable:
      variable || (gatewayAvailable || oauthAvailable ? null : variables[0]),
    ...(provider === "openai" && !variable && !gatewayAvailable
      ? { canSignIn: true }
      : {}),
  };
}

function resolveGatewayEnvironment(
  env,
  { platform = process.platform, execFileSync = execFileSyncProcess } = {},
) {
  if (env.LANGSMITH_GATEWAY_API_KEY || platform !== "darwin") return {};
  try {
    const key = execFileSync("/bin/launchctl", ["getenv", "LC_GATEWAY_KEY"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 1_000,
    }).trim();
    if (!key) return {};
    return {
      LANGSMITH_GATEWAY_API_KEY: key,
      ...(env.LANGSMITH_GATEWAY_ENABLED === undefined
        ? { LANGSMITH_GATEWAY_ENABLED: "true" }
        : {}),
    };
  } catch {
    return {};
  }
}

class BackendSupervisor {
  constructor(options) {
    this.options = options;
    this.spawn = options.spawn || spawnProcess;
    this.fetch = options.fetch || fetch;
    this.reservePort = options.reservePort || reservePort;
    this.child = null;
    this.port = null;
    this.token = null;
    this.logs = "";
    this.closing = false;
    this.ready = null;
    this.failure = null;
    this.gatewayEnv = null;
  }

  gatewayEnvironment() {
    if (this.gatewayEnv) return this.gatewayEnv;
    const env = { ...process.env, ...this.options.env };
    this.gatewayEnv = resolveGatewayEnvironment(
      env,
      this.options.gatewayEnvironment,
    );
    return this.gatewayEnv;
  }

  pidFile() {
    return this.options.stateDir
      ? path.join(this.options.stateDir, PID_FILE)
      : null;
  }

  reapStaleBackend() {
    const file = this.pidFile();
    if (!file) return;
    try {
      const pid = Number.parseInt(fs.readFileSync(file, "utf8"), 10);
      if (Number.isInteger(pid) && pid > 0 && isLangGraphProcess(pid)) {
        killProcessTree(pid, "SIGTERM");
      }
    } catch {}
    try {
      fs.unlinkSync(file);
    } catch {}
  }

  start() {
    if (this.ready && !this.failure) return this.ready;
    this.ready = this.startOnce().catch((error) => {
      this.ready = null;
      throw error;
    });
    return this.ready;
  }

  async startOnce() {
    this.closing = false;
    this.failure = null;
    this.logs = "";
    this.port = await this.reservePort(HOST);
    this.token = randomBytes(32).toString("base64url");
    const target = localBackendTarget({ ...this.options, port: this.port });
    if (!this.options.projectsFile)
      throw new Error("Local project allowlist is not configured");
    if (!this.options.worktreesDir)
      throw new Error("Local worktree directory is not configured");
    fs.mkdirSync(this.options.worktreesDir, { recursive: true });
    if (this.options.stateDir) {
      fs.mkdirSync(this.options.stateDir, { recursive: true });
      this.reapStaleBackend();
    }
    if (this.options.isPackaged && !fs.existsSync(target.command)) {
      throw new Error(`Bundled local backend is missing: ${target.command}`);
    }
    const child = this.spawn(target.command, target.args, {
      cwd: target.cwd,
      env: {
        ...process.env,
        ...this.options.env,
        ...this.gatewayEnvironment(),
        ...(await this.options.tracingEnv?.()),
        ...this.options.providerEnv?.(),
        OPEN_SWE_LOCAL_AUTH_TOKEN: this.token,
        OPEN_SWE_LOCAL_PROJECTS_FILE: this.options.projectsFile,
        OPEN_SWE_LOCAL_WORKTREES_DIR: this.options.worktreesDir,
        ...(this.options.stateDir
          ? {
              OPEN_SWE_LOCAL_ARTIFACTS_DIR: path.join(
                this.options.stateDir,
                "artifacts",
              ),
            }
          : {}),
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
      detached: process.platform !== "win32",
    });
    this.child = child;
    const pidFile = this.pidFile();
    if (pidFile && typeof child.pid === "number") {
      try {
        fs.writeFileSync(pidFile, `${child.pid}\n`);
      } catch {}
    }
    const append = (chunk) => {
      this.logs = `${this.logs}${chunk.toString("utf8")}`.slice(-16_000);
    };
    child.stdout?.on("data", append);
    child.stderr?.on("data", append);

    let startupError = null;
    const exited = new Promise((resolve) => {
      // A child that was already replaced must not disturb its successor.
      const fail = (error) => {
        if (this.child === child && !this.closing) {
          this.failure = error;
          this.child = null;
        }
      };
      child.once("error", (error) => {
        startupError = error;
        fail(error);
        resolve();
      });
      child.once("exit", (code, signal) => {
        if (!startupError) {
          const reason = signal ? `signal ${signal}` : `exit code ${code}`;
          startupError = new Error(
            `Local LangGraph backend stopped with ${reason}`,
          );
        }
        fail(startupError);
        resolve();
      });
    });
    const deadline =
      Date.now() + (this.options.startTimeoutMs || START_TIMEOUT_MS);
    while (Date.now() < deadline) {
      if (startupError) break;
      try {
        const response = await this.fetch(`http://${HOST}:${this.port}/`, {
          headers: { authorization: `Bearer ${this.token}` },
          signal: AbortSignal.timeout(1_000),
        });
        if (response.ok) {
          this.failure = null;
          return this.publicConfig();
        }
      } catch {}
      await Promise.race([delay(150), exited]);
    }
    await this.close();
    const detail = this.logs.trim();
    if (startupError) {
      throw new Error(`${startupError.message}${detail ? `\n${detail}` : ""}`);
    }
    throw new Error(
      `Local LangGraph backend did not become healthy${detail ? `\n${detail}` : ""}`,
    );
  }

  credentialStatus(modelId) {
    return modelCredentialStatus(
      modelId,
      {
        ...process.env,
        ...this.options.env,
        ...this.gatewayEnvironment(),
      },
      { openAiOAuth: this.options.openAiOAuthAvailable?.() === true },
    );
  }

  publicConfig() {
    return { apiUrl: "/local-graph", graphId: "agent" };
  }

  async request(pathname, init = {}) {
    await this.start();
    const headers = new Headers(init.headers);
    headers.set("authorization", `Bearer ${this.token}`);
    headers.set("accept-encoding", "identity");
    return this.fetch(`http://${HOST}:${this.port}${pathname}`, {
      ...init,
      headers,
    });
  }

  async threadActivity() {
    if (!this.child || !this.port || !this.token) return {};
    try {
      const response = await this.fetch(
        `http://${HOST}:${this.port}/threads/search`,
        {
          method: "POST",
          headers: {
            authorization: `Bearer ${this.token}`,
            "content-type": "application/json",
          },
          body: JSON.stringify({ limit: 1_000 }),
          signal: AbortSignal.timeout(2_000),
        },
      );
      if (!response.ok) return null;
      const threads = await response.json();
      if (!Array.isArray(threads)) return null;
      const activity = {};
      for (const thread of threads) {
        const status = THREAD_STATUS[thread?.status];
        if (status && typeof thread.thread_id === "string")
          activity[thread.thread_id] = status;
      }
      return activity;
    } catch {
      return null;
    }
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
    });
    if (!response.ok) {
      throw new Error(
        `Could not create local LangGraph thread (${response.status})`,
      );
    }
  }

  async deleteThread(threadId) {
    const response = await this.request(
      `/threads/${encodeURIComponent(threadId)}`,
      {
        method: "DELETE",
      },
    );
    if (!response.ok && response.status !== 404) {
      throw new Error(
        `Could not delete local LangGraph thread (${response.status})`,
      );
    }
  }

  async proxy(request, prefix = "/local-graph") {
    const source = new URL(request.url);
    if (
      source.pathname !== prefix &&
      !source.pathname.startsWith(`${prefix}/`)
    ) {
      return new Response("Not found", { status: 404 });
    }
    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("cookie");
    const body = ["GET", "HEAD"].includes(request.method)
      ? undefined
      : request.body;
    return this.request(
      `${source.pathname.slice(prefix.length) || "/"}${source.search}`,
      {
        method: request.method,
        headers,
        body,
        redirect: "manual",
        ...(body ? { duplex: "half" } : {}),
      },
    );
  }

  /** Last resort for exit paths that cannot wait: signal and move on. */
  killSync() {
    const child = this.child;
    this.child = null;
    this.ready = null;
    const pidFile = this.pidFile();
    if (pidFile) {
      try {
        fs.unlinkSync(pidFile);
      } catch {}
    }
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    try {
      terminate(child, "SIGTERM");
    } catch {}
  }

  async close() {
    if (this.closing) return;
    this.closing = true;
    const child = this.child;
    this.port = null;
    this.token = null;
    this.failure = null;
    if (!child || child.exitCode !== null || child.signalCode !== null) {
      this.killSync();
      return;
    }
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        try {
          terminate(child, "SIGKILL");
        } catch {}
        resolve();
      }, this.options.stopTimeoutMs || STOP_TIMEOUT_MS);
      timer.unref?.();
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      this.killSync();
    });
  }
}

module.exports = {
  BackendSupervisor,
  devBackendTarget,
  localBackendTarget,
  modelCredentialStatus,
  packagedBackendTarget,
  reservePort,
};
