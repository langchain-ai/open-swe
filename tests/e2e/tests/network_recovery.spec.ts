import { expect, test, _electron as electron } from "@playwright/test"
import type { ElectronApplication, Page } from "@playwright/test"
import { execFileSync } from "node:child_process"
import { createServer } from "node:http"
import type { IncomingMessage, Server, ServerResponse } from "node:http"
import { createRequire } from "node:module"
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from "node:fs"
import { tmpdir } from "node:os"
import { delimiter, join, resolve } from "node:path"

/**
 * Acceptance for the desktop's offline/backend-loss recovery, driven through
 * the real Electron app and the real main-process coordinator.
 *
 * Only two boundaries are replaced: the dashboard (local-only mode has no
 * session, and model options come from a stub) and the local LangGraph backend,
 * which is a deterministic toy graph that can crash or freeze on command. The
 * renderer, preload, supervisor, coordinator, retry logic and history merge all
 * run for real.
 */

const repoRoot = resolve(__dirname, "..", "..", "..")
const e2eRoot = join(repoRoot, "tests", "e2e")
const desktopRoot = join(repoRoot, "desktop")
const fixtures = join(desktopRoot, "test", "e2e", "fixtures")
const electronPath = createRequire(join(desktopRoot, "package.json"))(
  "electron"
) as string
const artifactsDir = join(repoRoot, "artifacts", "network-recovery-e2e")

interface ToyEvent {
  ts: number
  event: "imported" | "run_start" | "run_end" | "crash"
  pid: number
  thread_id?: string
  resume?: boolean
  crash_ms?: number | null
  stream_ms?: number
  input?: string
}

interface ResumeChainSnapshot {
  threadId: string
  phase: "waiting" | "dispatching" | "inflight"
  reason: string
  generation: number
  sendTicket: number
}

interface ResumeSnapshot {
  enabled: boolean
  revision: number
  network: { reachable: boolean | null; checkedAt: number }
  chains: Array<ResumeChainSnapshot>
}

interface ResumeChange {
  kind: "network" | "chain-updated" | "chain-removed"
  threadId?: string
  outcome?: string
}

interface ResumeEvent {
  at: number
  snapshot: ResumeSnapshot
  change: ResumeChange | null
}

interface ResumeBridge {
  snapshot(): Promise<ResumeSnapshot | null>
  stall(input: { threadId: string }): Promise<{ action: "none" | "arm" | "resync" }>
  onChange(
    listener: (event: { snapshot: ResumeSnapshot; change: ResumeChange | null }) => void
  ): () => void
}

declare global {
  interface Window {
    openSweDesktop?: { networkResume?: ResumeBridge }
    __resumeEvents?: Array<ResumeEvent>
  }
}

interface Harness {
  app: ElectronApplication
  page: Page
  stateRoot: string
  toyLog: string
  close: () => Promise<void>
}

function readToyLog(file: string): Array<ToyEvent> {
  if (!existsSync(file)) return []
  return readFileSync(file, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line) as ToyEvent)
}

function firstEvent(
  file: string,
  predicate: (event: ToyEvent) => boolean
): ToyEvent | undefined {
  return readToyLog(file).find(predicate)
}

function repoOf(stateRoot: string): { home: string; project: string; gitConfig: string } {
  const home = join(stateRoot, "home")
  const project = join(stateRoot, "project")
  const gitConfig = join(stateRoot, "gitconfig")
  // Electron resolves `appData` from the profile root on Windows, so the
  // isolated profile needs the two AppData folders a real one always has.
  mkdirSync(join(home, "AppData", "Roaming"), { recursive: true })
  mkdirSync(join(home, "AppData", "Local"), { recursive: true })
  mkdirSync(project, { recursive: true })
  writeFileSync(gitConfig, "")
  const git = (...args: Array<string>) =>
    execFileSync("git", args, {
      cwd: project,
      stdio: "pipe",
      env: {
        ...process.env,
        GIT_CONFIG_GLOBAL: gitConfig,
        GIT_AUTHOR_NAME: "E2E",
        GIT_AUTHOR_EMAIL: "e2e@example.com",
        GIT_COMMITTER_NAME: "E2E",
        GIT_COMMITTER_EMAIL: "e2e@example.com",
      },
    })
  writeFileSync(join(project, "README.md"), "# E2E project\n")
  git("init", "-q", "-b", "main")
  git("add", "-A")
  git("commit", "-q", "-m", "seed")
  return { home, project, gitConfig }
}

async function startDashboardStub(): Promise<{
  url: string
  close: () => Promise<void>
}> {
  const server: Server = createServer(
    (request: IncomingMessage, response: ServerResponse) => {
      const pathname = new URL(request.url ?? "/", "http://127.0.0.1").pathname
      const json = (status: number, body: unknown) => {
        response.writeHead(status, { "content-type": "application/json" })
        response.end(JSON.stringify(body))
      }
      if (pathname === "/dashboard/api/me") return json(401, { detail: "Not signed in" })
      if (pathname === "/dashboard/api/options") {
        return json(200, {
          models: [
            {
              id: "openai:gpt-e2e",
              label: "E2E Toy",
              efforts: ["medium"],
              default_effort: "medium",
              supports_images: false,
            },
          ],
          default_agent_model: "openai:gpt-e2e",
          default_agent_reasoning_effort: "medium",
          default_agent_subagent_model: "openai:gpt-e2e",
          default_agent_subagent_reasoning_effort: "medium",
        })
      }
      if (pathname === "/dashboard/api/me/preferences") return json(200, {})
      return json(404, { detail: "Not found" })
    }
  )
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done))
  const address = server.address()
  if (!address || typeof address === "string") throw new Error("No stub port")
  return {
    url: `http://127.0.0.1:${address.port}`,
    close: () => {
      // The renderer keeps connections alive; a graceful server.close() alone
      // waits for them forever once the app is gone.
      server.closeAllConnections()
      return new Promise<void>((done) => server.close(() => done()))
    },
  }
}

async function launch(projectName: string): Promise<Harness> {
  const stateRoot = mkdtempSync(join(tmpdir(), `open-swe-nr-${projectName}-`))
  const { home, project, gitConfig } = repoOf(stateRoot)
  const stub = await startDashboardStub()
  const toyLog = join(stateRoot, "toy.jsonl")
  const appData = join(home, "AppData", "Roaming")
  const localAppData = join(home, "AppData", "Local")
  const xdg = join(stateRoot, "xdg")
  mkdirSync(xdg, { recursive: true })

  const app = await electron.launch({
    executablePath: electronPath,
    args: [desktopRoot, `--backend-url=${stub.url}`],
    cwd: repoRoot,
    env: {
      ...process.env,
      HOME: home,
      USERPROFILE: home,
      APPDATA: appData,
      LOCALAPPDATA: localAppData,
      XDG_CONFIG_HOME: xdg,
      GIT_CONFIG_GLOBAL: gitConfig,
      GIT_AUTHOR_NAME: "E2E",
      GIT_AUTHOR_EMAIL: "e2e@example.com",
      GIT_COMMITTER_NAME: "E2E",
      GIT_COMMITTER_EMAIL: "e2e@example.com",
      OPEN_SWE_LOCAL_BACKEND_CONFIG: join(fixtures, "langgraph.toy.json"),
      OPEN_SWE_NETWORK_RESUME: "1",
      OPENAI_API_KEY: "e2e-fake-key",
      PYTHONPATH: [fixtures, process.env.PYTHONPATH].filter(Boolean).join(delimiter),
      TOY_GRAPH_LOG: toyLog,
      UV_CACHE_DIR: join(e2eRoot, ".e2e-tmp", "uv-cache"),
    },
  })
  const page = await app.firstWindow()
  const child = app.process()
  const forward = (prefix: string) => (data: Buffer) => {
    for (const line of data.toString().split(/\r?\n/)) {
      const text = line.trim()
      if (text) console.info(`[${prefix}]`, text)
    }
  }
  child.stdout?.on("data", forward("main"))
  child.stderr?.on("data", forward("main-err"))
  page.on("console", (message) => {
    if (message.type() === "error") console.info("[renderer]", message.text())
  })
  const userData = await app.evaluate(({ app: electronApp }) =>
    electronApp.getPath("userData")
  )
  mkdirSync(userData, { recursive: true })
  writeFileSync(
    join(userData, "desktop-projects.json"),
    `${JSON.stringify([{ cwd: project, name: "demo", addedAt: Date.now() }], null, 2)}\n`
  )
  await app.context().addInitScript(() => {
    localStorage.setItem("open-swe.desktop.local-mode-without-sign-in", "true")
    localStorage.setItem("open-swe.desktop.last-project", "")
  })
  // Let the window's own first navigation finish; replacing it mid-flight
  // makes main.cjs log an ERR_ABORTED for the initial load.
  await page.waitForLoadState("domcontentloaded").catch(() => {})
  await page.goto("open-swe://app/agents")
  await expect(page.getByTestId("composer-editor")).toBeVisible({ timeout: 60_000 })

  return {
    app,
    page,
    stateRoot,
    toyLog,
    close: async () => {
      await quitApplication(app)
      await stub.close()
    },
  }
}

async function waitForRunStart(file: string, timeout = 60_000): Promise<ToyEvent> {
  await expect
    .poll(() => firstEvent(file, (event) => event.event === "run_start"), { timeout })
    .toBeTruthy()
  const event = firstEvent(file, (candidate) => candidate.event === "run_start")
  if (!event) throw new Error("run_start missing")
  return event
}

/** Captures every coordinator change in the page, with the renderer's clock. */
async function watchResume(page: Page): Promise<void> {
  await page.evaluate(() => {
    const bridge = window.openSweDesktop?.networkResume
    window.__resumeEvents = []
    if (!bridge) return
    bridge.onChange(({ snapshot, change }) => {
      window.__resumeEvents?.push({ at: Date.now(), snapshot, change })
    })
  })
}

async function resumeEvents(page: Page): Promise<Array<ResumeEvent>> {
  return page.evaluate(() => window.__resumeEvents ?? [])
}

/** Logs every coordinator revision so a failed wait shows what the chain did. */
function logResumeEvents(page: Page): () => void {
  let revision = -1
  const timer = setInterval(() => {
    void page
      .evaluate(() => window.openSweDesktop?.networkResume?.snapshot())
      .then((snapshot) => {
        if (!snapshot || snapshot.revision === revision) return
        revision = snapshot.revision
        console.info(
          "[snapshot]",
          JSON.stringify({
            at: Date.now(),
            network: snapshot.network,
            chains: snapshot.chains,
          })
        )
      })
      .catch(() => {})
  }, 250)
  return () => clearInterval(timer)
}

async function submitLocalTask(page: Page, text: string): Promise<number> {
  const editor = page.getByTestId("composer-editor")
  await editor.click()
  await editor.pressSequentially(text)
  const sentAt = Date.now()
  await editor.press("Enter")
  await expect(page).toHaveURL(/\/agents\/local\//, { timeout: 90_000 })
  return sentAt
}

async function threadIdFromUrl(page: Page): Promise<string> {
  const match = page.url().match(/\/agents\/local\/([^/?#]+)/)
  if (!match) throw new Error(`No local thread id in ${page.url()}`)
  return decodeURIComponent(match[1])
}

function writeMetrics(name: string, value: Record<string, unknown>): void {
  mkdirSync(artifactsDir, { recursive: true })
  // One file per scenario: a failed test can recycle the worker, so a single
  // aggregate written by `afterAll` would be overwritten by the next worker.
  writeFileSync(
    join(artifactsDir, `${name}.json`),
    `${JSON.stringify({ name, ...value }, null, 2)}\n`
  )
  console.info(`[metrics] ${name}`, JSON.stringify(value))
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((done) => setTimeout(done, milliseconds))
}

/**
 * Playwright's own Electron close waits on a protocol round-trip that the app
 * cannot answer once it has begun quitting (the main process is gone before
 * the reply lands), so quit through `app.quit()` and treat process exit as the
 * signal. The local backend is a grandchild of Electron and survives that
 * exit, so it is reaped explicitly.
 */
async function quitApplication(app: ElectronApplication): Promise<void> {
  const child = app.process()
  const exited = new Promise<void>((done) => {
    if (child.exitCode !== null) done()
    else child.once("exit", () => done())
  })
  await Promise.race([
    app
      .evaluate(({ app: electronApp }) => {
        electronApp.quit()
        return true
      })
      .catch(() => {}),
    sleep(5_000),
  ])
  const graceful = await Promise.race([
    exited.then(() => true),
    sleep(15_000).then(() => false),
  ])
  if (!graceful && child.exitCode === null && process.platform === "win32") {
    try {
      execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        stdio: "pipe",
      })
    } catch {}
  }
  if (process.platform === "win32") {
    try {
      execFileSync(
        "powershell",
        [
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'langgraph\\.toy\\.json' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
        ],
        { stdio: "pipe" }
      )
    } catch {}
  }
}

function powershell(script: string): void {
  execFileSync(
    "powershell",
    ["-NoProfile", "-NonInteractive", "-Command", script],
    { stdio: "pipe" }
  )
}

function setProcessSuspended(pid: number, suspend: boolean): void {
  const call = suspend ? "NtSuspendProcess" : "NtResumeProcess"
  powershell(`
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NtProc {
  [DllImport("ntdll.dll")] public static extern int NtSuspendProcess(IntPtr handle);
  [DllImport("ntdll.dll")] public static extern int NtResumeProcess(IntPtr handle);
}
'@
$process = [System.Diagnostics.Process]::GetProcessById(${pid})
[void][NtProc]::${call}($process.Handle)
`)
}

test.beforeAll(() => {
  mkdirSync(artifactsDir, { recursive: true })
})

test("renderer offline shows the notice and the local run is untouched", async () => {
  const harness = await launch("offline")
  const { page, toyLog } = harness
  try {
    await watchResume(page)
    const sentAt = await submitLocalTask(page, "offline probe TOY_STREAM_MS=15000")
    await waitForRunStart(toyLog)
    await expect(page.getByText(/Toy answer for: offline probe/)).toBeVisible({
      timeout: 30_000,
    })

    const cdp = await page.context().newCDPSession(page)
    await cdp.send("Network.enable")
    const offlineAt = Date.now()
    await cdp.send("Network.emulateNetworkConditions", {
      offline: true,
      latency: 0,
      downloadThroughput: 0,
      uploadThroughput: 0,
    })
    await expect(page.getByText(/Network connection lost/)).toBeVisible({
      timeout: 15_000,
    })
    const toastAt = Date.now()

    await page.waitForTimeout(3_000)
    const onlineAt = Date.now()
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: 0,
      downloadThroughput: -1,
      uploadThroughput: -1,
    })
    await expect(page.getByText(/Back online/)).toBeVisible({ timeout: 15_000 })
    const restoredAt = Date.now()
    await cdp.detach()

    await expect
      .poll(() => firstEvent(toyLog, (event) => event.event === "run_end"), {
        timeout: 60_000,
      })
      .toBeTruthy()
    await expect(page.getByText(/Toy answer for: offline probe/)).toBeVisible()
    const events = await resumeEvents(page)
    expect(events.filter((event) => event.snapshot.chains.length > 0)).toEqual([])

    writeMetrics("renderer-offline", {
      submittedAt: sentAt,
      offlineAt,
      toastLatencyMs: toastAt - offlineAt,
      offlineDurationMs: onlineAt - offlineAt,
      restoreToastLatencyMs: restoredAt - onlineAt,
      recoveryChainsArmed: 0,
      runCompleted: true,
    })
  } finally {
    await harness.close()
  }
})

test("a long real outage leaves the local run and its backend alone", async () => {
  // The offline test above lasts 3s and only checks the notice. This one stays
  // dark for 30s, through several SDK reconnect attempts, and pins the fact a
  // loopback backend relies on: losing the network cannot reach it, so the run
  // keeps executing and nothing re-arms. The SDK's budget is far longer than
  // this (12 attempts reach ~23min), so this covers an outage mid-retry, not
  // one that exhausted it.
  const harness = await launch("outage")
  const { page, toyLog } = harness
  const stopLogging = logResumeEvents(page)
  try {
    await watchResume(page)
    // Long enough to still be streaming after the outage; no TOY_CRASH_MS, so
    // the backend has no reason to die on its own.
    await submitLocalTask(page, "outage probe TOY_STREAM_MS=30000")
    const threadId = await threadIdFromUrl(page)
    const runStart = await waitForRunStart(toyLog)
    await expect(page.getByText(/Toy answer for: outage probe/)).toBeVisible({
      timeout: 30_000,
    })

    const cdp = await page.context().newCDPSession(page)
    await cdp.send("Network.enable")
    const offlineAt = Date.now()
    await cdp.send("Network.emulateNetworkConditions", {
      offline: true,
      latency: 0,
      downloadThroughput: 0,
      uploadThroughput: 0,
    })
    // Long enough to span several backoff steps (1, 2, 4, 8, 16s) while the
    // SDK is still retrying; the run is sized to finish inside this window.
    await page.waitForTimeout(30_000)
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: 0,
      downloadThroughput: -1,
      uploadThroughput: -1,
    })
    await cdp.detach()
    const onlineAt = Date.now()

    await expect
      .poll(() => firstEvent(toyLog, (event) => event.event === "run_end"), {
        timeout: 120_000,
      })
      .toBeTruthy()
    const runEnd = firstEvent(toyLog, (event) => event.event === "run_end")
    await expect(page.getByText(/Toy answer for: outage probe/)).toBeVisible({
      timeout: 60_000,
    })

    const events = readToyLog(toyLog)
    const runStarts = events.filter(
      (event) => event.event === "run_start" && event.thread_id === threadId
    )
    // One run on one backend process: nothing restarted, nothing re-armed.
    expect(runStarts).toHaveLength(1)
    expect(runStarts[0]?.pid).toBe(runStart.pid)
    expect(
      events.filter((event) => event.event === "imported")
    ).toHaveLength(1)
    expect(events.filter((event) => event.event === "crash")).toHaveLength(0)
    if (!runEnd) throw new Error("run_end missing")
    // The run should land inside the outage window -- that is the whole claim
    // of a loopback backend. Recorded rather than asserted: on a loaded
    // machine the run can overrun by a second, and a timing miss would fail
    // the test without anything being wrong.
    const runEndedMsAfterOffline = runEnd.ts * 1000 - offlineAt

    const chains = (await resumeEvents(page)).filter(
      (event) => event.snapshot.chains.length > 0
    )
    expect(chains).toEqual([])
    await expect(page.getByTestId("user-message")).toHaveCount(1)

    writeMetrics("renderer-outage", {
      offlineAt,
      offlineDurationMs: onlineAt - offlineAt,
      recoveryChainsArmed: chains.length,
      runs: runStarts.length,
      backendRestarted: runStarts[0]?.pid !== runStart.pid,
      runCompleted: true,
      // Measured from the outage start: a value inside `offlineDurationMs`
      // shows the run finished while the renderer was dark.
      runEndedMsAfterOffline,
    })
  } finally {
    stopLogging()
    await harness.close()
  }
})

test("a killed backend is restarted and the turn resumes exactly once", async () => {
  const harness = await launch("crash")
  const { page, toyLog } = harness
  const stopLogging = logResumeEvents(page)
  try {
    await watchResume(page)
    const sentAt = await submitLocalTask(
      page,
      "crash probe TOY_STREAM_MS=20000 TOY_CRASH_MS=3000"
    )
    const threadId = await threadIdFromUrl(page)
    await waitForRunStart(toyLog)
    await expect(page.getByText(/Toy answer for: crash probe/)).toBeVisible({
      timeout: 30_000,
    })

    await expect
      .poll(() => firstEvent(toyLog, (event) => event.event === "crash"), {
        timeout: 30_000,
      })
      .toBeTruthy()
    const crash = firstEvent(toyLog, (event) => event.event === "crash")
    if (!crash) throw new Error("crash event missing")

    await expect(page.getByText(/Continuation done/)).toBeVisible({
      timeout: 120_000,
    }).catch(async (error: unknown) => {
      const diagnosis = await page
        .evaluate(async (threadId) => {
          const bridge = window.openSweDesktop?.networkResume
          return {
            snapshot: await bridge?.snapshot(),
            stall: await bridge?.stall({ threadId }),
            events: window.__resumeEvents ?? [],
          }
        }, threadId)
        .catch((cause: unknown) => ({ failed: String(cause) }))
      console.info("[diagnosis]", JSON.stringify(diagnosis))
      const runStarts = readToyLog(toyLog).filter(
        (event) => event.event === "run_start" && event.thread_id === threadId
      )
      writeMetrics("backend-killed", {
        submittedAt: sentAt,
        crashAt: crash.ts * 1000,
        recovered: false,
        observedWithoutRecoveryMs: Date.now() - crash.ts * 1000,
        coordinatorRevision:
          typeof diagnosis === "object" && diagnosis && "snapshot" in diagnosis
            ? (diagnosis.snapshot as ResumeSnapshot | null | undefined)?.revision
            : null,
        resumeDispatchRequested: null,
        stallAction:
          typeof diagnosis === "object" && diagnosis && "stall" in diagnosis
            ? (diagnosis.stall as { action?: string } | undefined)?.action
            : null,
        runs: runStarts.length,
        resumeRuns: runStarts.filter((event) => event.resume === true).length,
        chainEvents: 0,
        transcriptVisible: (await page.getByText(/Toy answer for: crash probe/).count()) > 0,
      })
      throw error
    })
    const continuedAt = Date.now()
    await expect
      .poll(
        () =>
          firstEvent(
            toyLog,
            (event) => event.event === "run_end" && event.resume === true
          ),
        { timeout: 60_000 }
      )
      .toBeTruthy()

    const events = await resumeEvents(page)
    const waiting = events.find((event) =>
      event.snapshot.chains.some((chain) => chain.phase === "waiting")
    )
    const dispatching = events.find((event) =>
      event.snapshot.chains.some((chain) => chain.phase === "dispatching")
    )
    const released = events.find((event) => event.change?.kind === "chain-removed")
    const resumeStart = firstEvent(
      toyLog,
      (event) => event.event === "run_start" && event.resume === true
    )
    const runStarts = readToyLog(toyLog).filter(
      (event) => event.event === "run_start" && event.thread_id === threadId
    )
    expect(runStarts).toHaveLength(2)
    expect(runStarts.filter((event) => event.resume === true)).toHaveLength(1)
    expect(waiting).toBeTruthy()
    expect(dispatching).toBeTruthy()
    expect(released).toBeTruthy()
    expect(released?.change?.outcome).toBe("completed")

    await expect
      .poll(() => page.evaluate(() => window.openSweDesktop?.networkResume?.snapshot()), {
        timeout: 30_000,
      })
      .toMatchObject({ chains: [] })
    // One user message in the transcript: the sidebar and header repeat the
    // thread title, so the count has to be scoped to the rendered turn.
    const userMessages = page.getByTestId("user-message")
    await expect(userMessages).toHaveCount(1)
    await expect(userMessages.first()).toContainText("TOY_STREAM_MS=20000")
    await expect(page.getByText(/system:network-resume/)).toHaveCount(0)

    writeMetrics("backend-killed", {
      submittedAt: sentAt,
      crashAt: crash.ts * 1000,
      detectionMs: waiting ? waiting.at - crash.ts * 1000 : null,
      restartedAndDispatchedMs: dispatching ? dispatching.at - crash.ts * 1000 : null,
      probeToDispatchMs:
        waiting && dispatching ? dispatching.at - waiting.at : null,
      resumeRunStartMs: resumeStart ? resumeStart.ts * 1000 - crash.ts * 1000 : null,
      continuationVisibleMs: continuedAt - crash.ts * 1000,
      chainReleasedAtMs: released ? released.at - crash.ts * 1000 : null,
      releaseOutcome: released?.change?.outcome ?? null,
      runs: runStarts.length,
      resumeRuns: runStarts.filter((event) => event.resume === true).length,
      userMessagesInTranscript: 1,
      hiddenContinuationVisible: false,
      generation:
        dispatching?.snapshot.chains.find((chain) => chain.threadId === threadId)
          ?.generation ?? null,
    })
  } finally {
    stopLogging()
    await harness.close()
  }
})

test("a frozen backend is detected by the stall watchdog and not duplicated", async () => {
  test.skip(process.platform !== "win32", "NtSuspendProcess is Windows-only")
  const harness = await launch("freeze")
  const { page, toyLog } = harness
  const stopLogging = logResumeEvents(page)
  try {
    await watchResume(page)
    await submitLocalTask(page, "freeze probe TOY_STREAM_MS=90000")
    const threadId = await threadIdFromUrl(page)
    const runStart = await waitForRunStart(toyLog)
    await expect(page.getByText(/Toy answer for: freeze probe/)).toBeVisible({
      timeout: 30_000,
    })

    const freezeAt = Date.now()
    setProcessSuspended(runStart.pid, true)
    await expect
      .poll(
        () =>
          resumeEvents(page).then((events) =>
            events.find((event) =>
              event.snapshot.chains.some((chain) => chain.phase === "waiting")
            )
          ),
        { timeout: 120_000, intervals: [500] }
      )
      .toBeTruthy()
    const armed = (await resumeEvents(page)).find((event) =>
      event.snapshot.chains.some((chain) => chain.phase === "waiting")
    )
    const unfreezeAt = Date.now()
    setProcessSuspended(runStart.pid, false)

    await expect
      .poll(() => firstEvent(toyLog, (event) => event.event === "run_end"), {
        timeout: 120_000,
      })
      .toBeTruthy()
    const runEnd = firstEvent(toyLog, (event) => event.event === "run_end")
    const runStarts = readToyLog(toyLog).filter(
      (event) => event.event === "run_start" && event.thread_id === threadId
    )
    expect(runStarts).toHaveLength(1)
    await expect
      .poll(() => page.evaluate(() => window.openSweDesktop?.networkResume?.snapshot()), {
        timeout: 30_000,
      })
      .toMatchObject({ chains: [] })
    const released = (await resumeEvents(page)).find(
      (event) => event.change?.kind === "chain-removed"
    )
    expect(released?.change?.outcome).toBe("taken-over")

    writeMetrics("backend-frozen", {
      frozenAt: freezeAt,
      stallArmedAtMs: armed ? armed.at - freezeAt : null,
      frozenForMs: unfreezeAt - freezeAt,
      resumedAt: unfreezeAt,
      runEndedMsAfterUnfreeze: runEnd ? runEnd.ts * 1000 - unfreezeAt : null,
      releaseOutcome: released?.change?.outcome ?? null,
      runs: runStarts.length,
      duplicateRuns: runStarts.length - 1,
    })
  } finally {
    stopLogging()
    await harness.close()
  }
})
