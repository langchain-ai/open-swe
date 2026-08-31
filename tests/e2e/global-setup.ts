import { execFileSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

function listeningPids(port: string): number[] {
  let output: string;
  try {
    output = execFileSync(
      "lsof",
      ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"],
      { encoding: "utf8" },
    );
  } catch (error) {
    if ((error as { status?: number }).status === 1) return [];
    throw error;
  }
  return [...new Set(output.match(/\d+/g)?.map(Number) ?? [])];
}

export function reclaimStaleUiServer(port: string, server: string) {
  const listeners = listeningPids(port).map((pid) => ({
    pid,
    command: execFileSync("ps", ["-p", String(pid), "-o", "command="], {
      encoding: "utf8",
    }).trim(),
  }));
  const unrelated = listeners.find(({ command }) => !command.includes(server));
  if (unrelated) {
    throw new Error(
      `E2E UI port ${port} is already in use by PID ${unrelated.pid}`,
    );
  }
  for (const { pid } of listeners) process.kill(pid, "SIGKILL");
}

// Build the real ui/ app once, then run its Nitro server so the harness can
// proxy page requests to it — the tests exercise server rendering, not a
// prerendered shell. Both API bases are baked in at build time, so they must
// match the harness port. Set E2E_FORCE_UI_BUILD=1 to rebuild (e.g. after
// changing the port or the UI).
export default async function globalSetup() {
  const repoRoot = resolve(__dirname, "..", "..");
  const ui = resolve(repoRoot, "ui");
  const server = resolve(ui, ".output", "server", "index.mjs");
  const port = process.env.E2E_PORT ?? "2024";
  const uiPort = process.env.E2E_UI_PORT ?? "3100";
  const harness = `http://127.0.0.1:${port}`;

  // Reclaim only the Nitro server from a hard-killed prior run. Refuse to
  // terminate unrelated processes using the configured port.
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
        VITE_DASHBOARD_API_BASE_URL: harness,
        DASHBOARD_API_URL: harness,
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
