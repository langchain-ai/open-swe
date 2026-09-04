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
const THREAD_STATUS = { busy: "running", error: "error" };
const PROVIDER_KEYS = {
  anthropic: ["ANTHROPIC_API_KEY"],
  fireworks: ["FIREWORKS_API_KEY"],
  google_genai: ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
  openai: ["OPENAI_API_KEY"],
};
const GATEWAY_KEYS = [
  "LANGSMITH_GATEWAY_API_KEY",
  "LANGSMITH_API_KEY_PROD",
  "LANGSMITH_API_KEY",
];

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
    this.closed = null;
    this.restarting = null;
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

  start() {
    if (this.ready && this.child && !this.failure) return this.ready;
    this.ready = this.startOnce().catch((error) => {
      this.ready = null;
      throw error;
    });
    return this.ready;
  }

  async startOnce() {
    this.closing = false;
    this.closed = null;
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
    if (this.options.stateDir)
      fs.mkdirSync(this.options.stateDir, { recursive: true });
    if (this.options.isPackaged && !fs.existsSync(target.command)) {
      throw new Error(`Bundled local backend is missing: ${target.command}`);
    }
    const child = this.spawn(target.command, target.args, {
      cwd: target.cwd,
      env: {
        ...process.env,
        ...this.options.env,
        ...this.gatewayEnvironment(),
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
    });
    this.child = child;
    const append = (chunk) => {
      this.logs = `${this.logs}${chunk.toString("utf8")}`.slice(-16_000);
    };
    child.stdout?.on("data", append);
    child.stderr?.on("data", append);

    let startupError = null;
    const exited = new Promise((resolve) => {
      child.once("error", (error) => {
        startupError = error;
        if (!this.closing) this.failure = error;
        resolve();
      });
      child.once("exit", (code, signal) => {
        if (!startupError) {
          const reason = signal ? `signal ${signal}` : `exit code ${code}`;
          startupError = new Error(
            `Local LangGraph backend stopped with ${reason}`,
          );
        }
        if (!this.closing) this.failure = startupError;
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

  restart() {
    this.restarting = Promise.resolve(this.restarting)
      .catch(() => {})
      .then(async () => {
        if (!this.child) return;
        await this.close();
        await this.start();
      });
    return this.restarting;
  }

  credentialStatus(modelId) {
    return modelCredentialStatus(
      modelId,
      {
        ...process.env,
        ...this.options.env,
        ...this.gatewayEnvironment(),
        ...this.options.providerEnv?.(),
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

  close() {
    if (this.closing) return this.closed || Promise.resolve();
    this.closed = this.closeOnce();
    return this.closed;
  }

  async closeOnce() {
    this.closing = true;
    const child = this.child;
    this.child = null;
    this.port = null;
    this.token = null;
    this.ready = null;
    this.failure = null;
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch {}
        resolve();
      }, this.options.stopTimeoutMs || STOP_TIMEOUT_MS);
      timer.unref?.();
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      try {
        child.kill("SIGTERM");
      } catch {
        clearTimeout(timer);
        resolve();
      }
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
