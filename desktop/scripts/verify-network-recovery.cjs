/**
 * Live check of the network-recovery continuation against a real local backend.
 *
 *   node desktop/scripts/verify-network-recovery.cjs
 *
 * Starts `langgraph dev` with the desktop config, begins a run, kills the
 * backend mid-run, restarts it, then sends the continuation exactly as the
 * desktop would and reports whether the thread carried on.
 *
 * Needs `uv`, a configured model in `.env` (this machine points at ollama) and
 * `git`. Outputs (state, project, backend log) land in
 * `artifacts/network-recovery-live/` unless `OPEN_SWE_NETWORK_RECOVERY_OUT` says
 * otherwise; each run uses fresh directories because a killed backend leaves
 * sqlite files behind.
 */
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..", "..");
const { resumeNetworkRun } = require(
  path.join(repoRoot, "desktop", "src", "network-resume-run.cjs")
);

const baseDir =
  process.env.OPEN_SWE_NETWORK_RECOVERY_OUT ??
  path.join(repoRoot, "artifacts", "network-recovery-live");
const runId = new Date().toISOString().replace(/[:.]/g, "-");
const stateDir = path.join(baseDir, `state-${runId}`);
const projectDir = path.join(baseDir, `project-${runId}`);
const backendLog = path.join(baseDir, `backend-${runId}.log`);
const token = "network-recovery-live-token";
const promptPath = path.join(
  repoRoot,
  "agent/resources/prompts/runs/network-resume.md"
);
const TASK =
  "Write a four-line poem about a lighthouse. After the poem, add a line that starts with 'Mood:' and one sentence explaining the mood.";

function log(message, extra = {}) {
  const detail = Object.keys(extra).length ? ` ${JSON.stringify(extra)}` : "";
  console.log(`[${new Date().toISOString()}] ${message}${detail}`);
}

/** `uv run langgraph dev` reads `.env` from its cwd, so hand it over explicitly. */
function dotenv(file) {
  const values = {};
  if (!fs.existsSync(file)) return values;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (match) values[match[1]] = match[2].trim().replace(/^["']|["']$/g, "");
  }
  return values;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

function prepareWorkspace() {
  fs.mkdirSync(stateDir, { recursive: true });
  fs.mkdirSync(projectDir, { recursive: true });
  fs.writeFileSync(
    path.join(projectDir, "README.md"),
    "# Live recovery project\n\nUsed to verify the network-resume continuation.\n"
  );
  const git = (...args) =>
    execFileSync("git", args, {
      cwd: projectDir,
      stdio: "pipe",
      env: {
        ...process.env,
        GIT_AUTHOR_NAME: "Live Test",
        GIT_AUTHOR_EMAIL: "live@example.com",
        GIT_COMMITTER_NAME: "Live Test",
        GIT_COMMITTER_EMAIL: "live@example.com",
      },
    });
  git("init", "-q", "-b", "main");
  git("add", "-A");
  git("commit", "-q", "-m", "Seed the project");
  const projectsFile = path.join(stateDir, "desktop-projects.json");
  fs.writeFileSync(
    projectsFile,
    `${JSON.stringify([{ cwd: projectDir, name: "demo", addedAt: Date.now() }], null, 2)}\n`
  );
  return { projectsFile, worktreesDir: path.join(stateDir, "worktrees") };
}

function startBackend(port, { projectsFile, worktreesDir }, phase) {
  const stream = fs.createWriteStream(backendLog, { flags: "a" });
  stream.write(`\n===== ${phase} (port ${port}) =====\n`);
  const child = spawn(
    "uv",
    [
      "run",
      "--project",
      repoRoot,
      "langgraph",
      "dev",
      "--no-browser",
      "--no-reload",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--n-jobs-per-worker",
      "10",
      "--allow-blocking",
      "--config",
      path.join(repoRoot, "langgraph.desktop.json"),
    ],
    {
      cwd: stateDir,
      env: {
        ...dotenv(path.join(repoRoot, ".env")),
        ...process.env,
        OPEN_SWE_LOCAL_AUTH_TOKEN: token,
        OPEN_SWE_LOCAL_PROJECTS_FILE: projectsFile,
        OPEN_SWE_LOCAL_WORKTREES_DIR: worktreesDir,
        OPEN_SWE_LOCAL_ARTIFACTS_DIR: path.join(stateDir, "artifacts"),
        OPEN_SWE_LOCAL_CHECKPOINT_DB: path.join(stateDir, "checkpoints.sqlite"),
        PYTHONUNBUFFERED: "1",
        PYTHONUTF8: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    }
  );
  child.stdout?.on("data", (chunk) => stream.write(chunk));
  child.stderr?.on("data", (chunk) => stream.write(chunk));
  child.on("exit", (code, signal) =>
    log("backend exited", { phase, code, signal })
  );
  return child;
}

function api(port, pathname, init = {}) {
  const headers = new Headers(init.headers);
  headers.set("authorization", `Bearer ${token}`);
  if (init.body) headers.set("content-type", "application/json");
  return fetch(`http://127.0.0.1:${port}${pathname}`, { ...init, headers });
}

async function waitHealthy(port, deadlineMs = 120_000) {
  const deadline = Date.now() + deadlineMs;
  for (;;) {
    try {
      if ((await api(port, "/")).ok) return true;
    } catch {}
    if (Date.now() > deadline)
      throw new Error("The local backend never became healthy");
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

function killTree(child) {
  if (!child?.pid) return;
  try {
    if (process.platform === "win32")
      execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        stdio: "pipe",
      });
    else {
      process.kill(child.pid, "SIGKILL");
      for (const pid of execFileSync("pgrep", ["-P", String(child.pid)], {
        encoding: "utf8",
      })
        .split("\n")
        .filter(Boolean))
        process.kill(Number(pid), "SIGKILL");
    }
  } catch (error) {
    log("killing the backend failed", { error: String(error) });
  }
}

async function runStatus(port, threadId, runId_) {
  const response = await api(port, `/threads/${threadId}/runs/${runId_}`);
  if (!response.ok) return null;
  return response.json();
}

async function waitForRun(port, threadId, runId_, deadlineMs) {
  const deadline = Date.now() + deadlineMs;
  for (;;) {
    const run = await runStatus(port, threadId, runId_);
    if (run && run.status !== "pending" && run.status !== "running") return run;
    if (Date.now() > deadline) return run;
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
}

function describe(messages) {
  return messages.map((message) => {
    const text =
      typeof message.content === "string"
        ? message.content
        : JSON.stringify(message.content);
    return {
      type: message.type,
      sender: /sender="([^"]+)"/.exec(text)?.[1] ?? null,
      kind: /kind="([^"]+)"/.exec(text)?.[1] ?? null,
      text: text.replace(/\s+/g, " ").slice(0, 140),
    };
  });
}

async function main() {
  const { projectsFile, worktreesDir } = prepareWorkspace();
  const port = await freePort();
  const env = dotenv(path.join(repoRoot, ".env"));
  log("starting local backend", {
    port,
    model: env.LLM_MODEL_ID,
    baseUrl: env.OPENAI_BASE_URL,
  });

  let backend = startBackend(port, { projectsFile, worktreesDir }, "boot-1");
  try {
    await waitHealthy(port);
    log("backend healthy");

    const created = await api(port, "/threads", { method: "POST", body: "{}" });
    if (!created.ok)
      throw new Error(`Creating the thread failed (${created.status})`);
    const { thread_id: threadId } = await created.json();
    log("thread created", { threadId });

    const started = await api(port, `/threads/${threadId}/runs`, {
      method: "POST",
      body: JSON.stringify({
        assistant_id: "agent",
        input: { messages: [{ role: "user", content: TASK }] },
        config: {
          configurable: {
            source: "desktop",
            local_project_path: projectDir,
            model_selection: "auto",
          },
        },
      }),
    });
    if (!started.ok)
      throw new Error(
        `Starting the run failed (${started.status}): ${await started.text()}`
      );
    const first = await started.json();
    log("run started", { runId: first.run_id });

    // Kill it while it is genuinely in flight: the backend reports the thread
    // busy for the whole model call.
    const busyDeadline = Date.now() + 60_000;
    let sawBusy = false;
    while (Date.now() < busyDeadline) {
      const listing = await api(port, "/threads/search", {
        method: "POST",
        body: JSON.stringify({ limit: 10 }),
      });
      if (listing.ok) {
        const threads = await listing.json();
        if (
          threads.find((item) => item.thread_id === threadId)?.status === "busy"
        ) {
          sawBusy = true;
          break;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    log("thread reached busy", { sawBusy });
    await new Promise((resolve) => setTimeout(resolve, 2_500));

    killTree(backend);
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    log("backend killed mid-run");

    backend = startBackend(port, { projectsFile, worktreesDir }, "boot-2");
    await waitHealthy(port);
    log("backend restarted");

    const restored = await api(port, `/threads/${threadId}/state`);
    log("history after restart", {
      status: restored.status,
      described: restored.ok
        ? describe((await restored.json()).values?.messages ?? [])
        : "thread record lost (the continuation re-creates it)",
    });

    const result = await resumeNetworkRun({
      supervisor: {
        // After the crash no run is active; that is what the coordinator's
        // activity check is meant to see.
        threadActivity: async () => ({}),
        request: (pathname, init) => api(port, pathname, init),
      },
      thread: {
        id: threadId,
        cwd: projectDir,
        worktreePath: null,
        modelId: null,
        effort: null,
      },
      readPrompt: () => fs.readFileSync(promptPath, "utf8").trim(),
      resume: {
        threadId,
        reason: "backend-down",
        rootRunId: first.run_id,
        previousRunId: first.run_id,
        generation: 2,
      },
    });
    log("continuation dispatched", result);

    const finished =
      result.status === "dispatched" && result.runId
        ? await waitForRun(port, threadId, result.runId, 300_000)
        : null;
    log("continuation finished", {
      status: finished?.status ?? result.status,
      error: finished?.error ? String(finished.error).slice(0, 400) : null,
    });

    const final = await api(port, `/threads/${threadId}/state`);
    const messages = final.ok ? ((await final.json()).values?.messages ?? []) : [];
    log("final thread state", { messages: messages.length });
    for (const entry of describe(messages)) console.log(JSON.stringify(entry));
    const last = messages[messages.length - 1];
    console.log("\n--- final answer ---\n");
    console.log(typeof last?.content === "string" ? last.content : "(none)");

    const ok =
      finished?.status === "success" &&
      last?.type === "ai" &&
      typeof last.content === "string" &&
      last.content.trim().length > 0;
    console.log(`\n${ok ? "NETWORK RECOVERY OK" : "NETWORK RECOVERY FAILED"}`);
    if (!ok) {
      console.log("\n--- backend log tail ---\n");
      console.log(
        fs
          .readFileSync(backendLog, "utf8")
          .trim()
          .split("\n")
          .slice(-25)
          .join("\n")
      );
      process.exitCode = 1;
    }
  } finally {
    killTree(backend);
  }
}

main().catch((error) => {
  console.error("LIVE CHECK FAILED:", error);
  const text = fs.existsSync(backendLog)
    ? fs
        .readFileSync(backendLog, "utf8")
        .trim()
        .split("\n")
        .slice(-30)
        .join("\n")
    : "(no backend log)";
  console.error(text);
  process.exit(1);
});
