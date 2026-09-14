import { execFileSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

function reclaimStaleUiServer(port: string, server: string) {
  let output: string;
  try {
    output = execFileSync(
      "lsof",
      ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"],
      { encoding: "utf8" },
    );
  } catch (error) {
    if ((error as { status?: number }).status === 1) return;
    throw error;
  }
  const pids = new Set(output.match(/\d+/g) ?? []);
  for (const pid of pids) {
    const command = execFileSync("ps", ["-p", pid, "-o", "command="], {
      encoding: "utf8",
    }).trim();
    if (!command.includes(server)) {
      throw new Error(`E2E UI port ${port} is already in use by PID ${pid}`);
    }
  }
  for (const pid of pids) process.kill(Number(pid), "SIGKILL");
}

// Build the real ui/ app once, then run its Nitro server — the origin the specs
// drive, exactly as a deployed browser does. The client API base is left empty
// so its `/dashboard/api/*` calls are same-origin and reach the backend through
// the app server's own proxy handler, which is the only path a deployment has.
// Baking the harness in as the API base instead pointed the browser straight at
// the backend, so the suite never ran the proxy and stayed green while it was
// broken. E2E_HARNESS makes the built server front the harness's fake-SaaS and
// control routes too, keeping the specs on one origin.
// Set E2E_FORCE_UI_BUILD=1 to rebuild (e.g. after changing the port or the UI).
export default async function globalSetup() {
  const repoRoot = resolve(__dirname, "..", "..");
  const ui = resolve(repoRoot, "ui");
  const server = resolve(ui, ".output", "server", "index.mjs");
  const port = process.env.E2E_PORT ?? "2024";
  const uiPort = process.env.E2E_UI_PORT ?? "3100";
  const harness = `http://127.0.0.1:${port}`;

  reclaimStaleUiServer(uiPort, server);

  if (!existsSync(server) || process.env.E2E_FORCE_UI_BUILD) {
    if (!existsSync(resolve(ui, "node_modules"))) {
      execFileSync(
        "pnpm",
        ["install", "--frozen-lockfile", "--filter", "open-swe-dashboard..."],
        {
          cwd: repoRoot,
          stdio: "inherit",
        },
      );
    }
    execFileSync("pnpm", ["--filter", "open-swe-dashboard", "run", "build"], {
      cwd: repoRoot,
      stdio: "inherit",
      env: {
        ...process.env,
        VITE_DASHBOARD_API_BASE_URL: "",
        DASHBOARD_API_URL: harness,
        E2E_HARNESS: harness,
      },
    });
  }

  // DASHBOARD_API_URL is read per request, so the running server needs it too —
  // not just the build.
  const child = spawn("node", [server], {
    cwd: ui,
    stdio: "inherit",
    env: {
      ...process.env,
      HOST: "127.0.0.1",
      PORT: uiPort,
      DASHBOARD_API_URL: harness,
    },
  });
  child.on("exit", (code) => {
    if (code) throw new Error(`ui server exited with code ${code}`);
  });

  const deadline = Date.now() + 60_000;
  for (;;) {
    try {
      await fetch(`http://127.0.0.1:${uiPort}/login`);
      break;
    } catch (error) {
      if (Date.now() > deadline) {
        child.kill();
        throw error;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  return () => {
    child.kill();
  };
}
