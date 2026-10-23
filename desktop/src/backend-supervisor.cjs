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
// `langgraph dev` otherwise installs blockbuster, and building the agent graph
// resolves a path through the synchronous `os.getcwd` that deepagents' version
// helper calls on Windows — every request then fails with a 500.
const ALLOW_BLOCKING = "--allow-blocking";
const START_TIMEOUT_MS = 60_000;
const STOP_TIMEOUT_MS = 5_000;
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
      ALLOW_BLOCKING,
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
      ALLOW_BLOCKING,
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
    // Streaming responses outlive the request that opened them. Without this
    // bookkeeping a proxy response whose backend died would never end, leaving
    // the renderer stuck on `Working…` forever.
    this.inflight = new Set();
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
    // A start still awaiting its port or tracing settings has no child yet;
    // callers in that window must join it rather than spawn a second backend.
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
              OPEN_SWE_LOCAL_CHECKPOINT_DB: path.join(
                this.options.stateDir,
                "checkpoints.sqlite",
              ),
            }
          : {}),
        PYTHONUNBUFFERED: "1",
        // Python's UTF-8 mode. Without it, a Windows locale that is not UTF-8
        // (cp936, cp950, cp1252, …) makes langgraph_api decode its bundled
        // openapi.json with that codec while importing and the server aborts.
        PYTHONUTF8: "1",
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
        if (!this.closing) {
          this.failure = error;
          this.backendLost(error);
        }
        resolve();
      });
      child.once("exit", (code, signal) => {
        if (!startupError) {
          const reason = signal ? `signal ${signal}` : `exit code ${code}`;
          startupError = new Error(
            `Local LangGraph backend stopped with ${reason}`,
          );
        }
        if (!this.closing) {
          this.failure = startupError;
          this.backendLost(startupError);
        }
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
          await this.reconcileOrphanedRuns();
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

  /**
   * Nothing can be executing right after boot, so any run the backend still
   * reports as pending or running was orphaned by the process that died — or by
   * the last shutdown. The dev server restores its run registry from a snapshot
   * and never reconciles it, which leaves those threads busy forever: the next
   * run on them queues behind a run that has no worker and never starts.
   *
   * This runs before `start()` resolves, so no proxied request can race it.
   */
  async reconcileOrphanedRuns() {
    try {
      const response = await this.fetch(
        `http://${HOST}:${this.port}/runs/cancel?action=interrupt`,
        {
          method: "POST",
          headers: {
            authorization: `Bearer ${this.token}`,
            "content-type": "application/json",
          },
          body: JSON.stringify({ status: "all" }),
          signal: AbortSignal.timeout(5_000),
        },
      );
      if (!response.ok && response.status !== 204) {
        this.logs = `${this.logs}orphaned run reconciliation returned ${response.status}\n`.slice(
          -16_000,
        );
      }
    } catch (error) {
      // A backend that cannot reconcile is still usable, but the reason its
      // threads stay busy has to be visible in the boot log.
      this.logs = `${this.logs}orphaned run reconciliation failed: ${error instanceof Error ? error.message : String(error)}\n`.slice(
        -16_000,
      );
    }
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

  /** The backend process is gone: fail every response it was still streaming. */
  backendLost(error) {
    this.abortInflight();
    this.options.onExit?.(error);
  }

  abortInflight() {
    const aborted = this.inflight.size;
    if (aborted > 0) {
      // Recorded on the backend log so a dead-stream bug report shows whether the
      // exit actually failed the requests instead of leaving them hanging.
      this.logs = `${this.logs}aborted ${aborted} in-flight proxy response(s) on backend exit\n`.slice(
        -16_000
      );
    }
    for (const controller of this.inflight) {
      try {
        controller.abort();
      } catch {}
    }
    this.inflight.clear();
  }

  /**
   * Health probe for the resume coordinator. A missing child counts as
   * unreachable; starting the backend stays `start()`'s job.
   */
  async probe(timeoutMs = 1_000) {
    if (!this.child || !this.port || !this.token) {
      return { reachable: false, checkedAt: Date.now(), status: null };
    }
    try {
      const response = await this.fetch(`http://${HOST}:${this.port}/ok`, {
        headers: { authorization: `Bearer ${this.token}` },
        signal: AbortSignal.timeout(timeoutMs),
      });
      return {
        reachable: response.status < 500,
        checkedAt: Date.now(),
        status: response.status,
      };
    } catch {
      return { reachable: false, checkedAt: Date.now(), status: null };
    }
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
    const controller = new AbortController();
    this.inflight.add(controller);
    try {
      const response = await this.request(
        `${source.pathname.slice(prefix.length) || "/"}${source.search}`,
        {
          method: request.method,
          headers,
          body,
          redirect: "manual",
          signal: request.signal
            ? AbortSignal.any([controller.signal, request.signal])
            : controller.signal,
          ...(body ? { duplex: "half" } : {}),
        },
      );
      return this.trackResponse(response, controller);
    } catch (error) {
      this.inflight.delete(controller);
      throw error;
    }
  }

  /**
   * Keep an in-flight proxy response registered until its body really ends, so
   * a backend exit can abort it. Unregistering on `await` alone would leak one
   * entry per reconnect.
   */
  trackResponse(response, controller) {
    const release = () => this.inflight.delete(controller);
    if (
      !response.body ||
      [204, 205, 304].includes(response.status)
    ) {
      release();
      return response;
    }
    const reader = response.body.getReader();
    const stream = new ReadableStream({
      async pull(streamController) {
        try {
          const { done, value } = await reader.read();
          if (done) {
            release();
            streamController.close();
            return;
          }
          streamController.enqueue(value);
        } catch (error) {
          release();
          streamController.error(error);
        }
      },
      cancel(reason) {
        release();
        return reader.cancel(reason).catch(() => {});
      },
    });
    return new Response(stream, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  }

  async close() {
    if (this.closing) return;
    this.closing = true;
    const child = this.child;
    this.child = null;
    this.port = null;
    this.token = null;
    this.ready = null;
    this.failure = null;
    this.abortInflight();
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
