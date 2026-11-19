import { parseArgs } from "node:util"
import { randomUUID } from "node:crypto"

import pkg from "../package.json"
import {
  ApiClient,
  ApiError,
  normalizeBackend,
  type TaskActivity,
} from "./api.ts"
import {
  forgetSession,
  readBackend,
  readConfig,
  storeSession,
} from "./config.ts"
import { SessionCredential } from "./credentials.ts"
import { originRepo, repoFullName } from "./git.ts"
import { composePrompt, readPipedStdin } from "./input.ts"
import { errorMessage, type JsonObject } from "./json.ts"
import { login } from "./login.ts"
import { followTask, type RunStatus } from "./follow.ts"

const USAGE = `robin — start a cloud Robin agent and wait for it

Usage:
  robin login [--backend <url>]      Sign in; --backend also repoints the desktop app
  robin logout                       Forget the session for the current backend
  robin auth status                  Show which credential is in use and check it
  robin run [options] [prompt...]    Start a cloud thread and wait for it to finish
  robin --help | --version

Run options:
  --thread <id>    Continue an existing thread
  --model <id>     Agent model id (requires --effort)
  --effort <name>  Reasoning effort for --model

A new thread works on the GitHub repository this directory's origin names.
Piped input is attached below the prompt, or is the prompt when none is given.
Stdout carries only the thread's dashboard URL. The exit code is 0 when the
agent's run completed, 1 when it failed or stopped to wait for an answer.
`

function out(text: string): void {
  process.stdout.write(`${text}\n`)
}

/** Stdout carries only the thread's URL, so everything else the CLI says goes here. */
function note(text: string): void {
  process.stderr.write(`${text}\n`)
}

function fail(text: string): void {
  process.stderr.write(`robin: ${text}\n`)
}

const INSTRUCTIONS_PREVIEW_CHARS = 72

/** Notes each worker as it changes state, and how many the CLI is waiting for. */
function workerProgress(): (activity: TaskActivity) => void {
  const states = new Map<string, string>()
  let waitingFor = 0
  return (activity) => {
    for (const worker of activity.workers) {
      const previous = states.get(worker.worker_thread_id)
      if (previous === worker.state) continue
      states.set(worker.worker_thread_id, worker.state)
      const reason = worker.error ?? worker.run_status
      const outcome = !worker.active && reason ? ` (${reason})` : ""
      const instructions = worker.instructions.split("\n")[0] ?? ""
      const preview =
        previous === undefined
          ? ` — ${instructions.slice(0, INSTRUCTIONS_PREVIEW_CHARS)}`
          : ""
      note(
        `Worker ${worker.state}${outcome}: ${worker.thread_url ?? worker.worker_thread_id}${preview}`
      )
    }
    const active = activity.workers.filter((worker) => worker.active).length
    if (active > 0 && active !== waitingFor)
      note(`Waiting for ${active} worker${active === 1 ? "" : "s"} to report`)
    waitingFor = active
  }
}

async function resolveBackend(explicit: string | undefined): Promise<string> {
  const candidate = explicit ?? (await readBackend())
  if (!candidate || !candidate.trim())
    throw new Error("a backend URL is required")
  return normalizeBackend(candidate)
}

async function loginCommand(
  backendOption: string | undefined
): Promise<number> {
  const backend = await resolveBackend(backendOption)
  const session = await login(backend)
  const path = await storeSession(backend, session, backendOption !== undefined)
  out(`Signed in to ${backend}. Session stored in ${path}.`)
  return 0
}

async function logoutCommand(): Promise<number> {
  const backend = normalizeBackend(await readBackend())
  out(
    (await forgetSession(backend))
      ? `Signed out of ${backend}.`
      : `No stored session for ${backend}.`
  )
  return 0
}

async function authStatusCommand(): Promise<number> {
  const config = await readConfig()
  if (config === null) {
    out(`Backend: ${normalizeBackend(await readBackend())}`)
    fail("not signed in — run `robin login`, or set OPEN_SWE_API_KEY")
    return 1
  }
  const { credential } = config
  out(`Backend: ${config.backend}`)
  out(`Credential: ${credential.source}`)
  const api = new ApiClient(config.backend, credential)
  try {
    if (!(credential instanceof SessionCredential)) {
      await api.verifyMachine()
      out("Accepted.")
    } else {
      const me = await api.me()
      const email = me.email ? ` <${me.email}>` : ""
      out(`Signed in as ${me.login}${email}${me.is_admin ? " (admin)" : ""}`)
    }
  } catch (cause) {
    if (!(cause instanceof ApiError && cause.status === 401)) throw cause
    fail(credential.rejected)
    return 1
  }
  return 0
}

interface RunOptions {
  thread: string | undefined
  model: string | undefined
  effort: string | undefined
  prompt: string
}

const STATUS_EXIT: Record<RunStatus, number> = {
  completed: 0,
  failed: 1,
  interrupted: 1,
}

async function runCommand(options: RunOptions): Promise<number> {
  const prompt = composePrompt(options.prompt, await readPipedStdin())
  if (!prompt) {
    fail("a prompt is required, as arguments or on stdin")
    return 2
  }
  const config = await readConfig()
  if (config === null) {
    fail("not signed in — run `robin login`, or set OPEN_SWE_API_KEY")
    return 1
  }
  const { credential } = config
  const api = new ApiClient(config.backend, credential)

  const creating = options.thread === undefined
  const threadId = options.thread ?? randomUUID()
  // Every machine run names its thread type, a resumed one included.
  const configurable: JsonObject = {
    thread_type: credential.machine ? "system" : "private",
  }
  // The repository is stamped on the thread when it is created; the server
  // rejects it on any later run.
  if (creating) {
    const repo = await originRepo(process.cwd())
    if (repo !== null) configurable["repo"] = repoFullName(repo)
    else {
      configurable["repo_explicitly_none"] = true
      note("No GitHub origin here; the thread starts without a repository.")
    }
  }
  if (options.model !== undefined) {
    configurable["agent_model_id"] = options.model
    if (options.effort !== undefined)
      configurable["agent_effort"] = options.effort
  }

  const threadUrl = api.dashboardUrl(`/agents/${encodeURIComponent(threadId)}`)
  const thread = api.coordinator(threadId)

  // 128 + SIGINT, as shells report an interrupted command, so `robin run … &&` stops.
  const INTERRUPTED = 130
  let interrupts = 0
  let following = false

  process.on("SIGINT", () => {
    interrupts += 1
    if (interrupts > 1 || !following) process.exit(INTERRUPTED)
    note(
      "\nStopping the run and its workers — press Ctrl-C again to exit immediately."
    )
    void thread
      .stop()
      .catch((cause: unknown) =>
        fail(`could not cancel the run and its workers: ${errorMessage(cause)}`)
      )
      .finally(() => process.exit(INTERRUPTED))
  })

  let status: RunStatus
  try {
    following = true
    const runId = await api.startRun(threadId, configurable, prompt)
    out(threadUrl)
    status = await followTask(thread, runId, {
      onRetry: (attempt, reason) =>
        note(
          `Lost the connection to the backend (${reason}); retrying, attempt ${attempt}`
        ),
      onActivity: workerProgress(),
    })
  } catch (cause) {
    fail(
      cause instanceof ApiError && cause.status === 401
        ? credential.rejected
        : errorMessage(cause)
    )
    return 1
  } finally {
    following = false
  }

  if (interrupts > 0) return INTERRUPTED
  if (status !== "completed") fail(`run ${status}; see ${threadUrl}`)
  return STATUS_EXIT[status]
}

const CLI_OPTIONS = {
  backend: { type: "string" },
  thread: { type: "string" },
  model: { type: "string" },
  effort: { type: "string" },
  help: { type: "boolean", short: "h" },
  version: { type: "boolean", short: "v" },
} as const

function parseCli(argv: readonly string[]) {
  return parseArgs({
    args: [...argv],
    allowPositionals: true,
    options: CLI_OPTIONS,
  })
}

export async function main(argv: readonly string[]): Promise<number> {
  let parsed: ReturnType<typeof parseCli>
  try {
    parsed = parseCli(argv)
  } catch (cause) {
    fail(errorMessage(cause))
    process.stderr.write(USAGE)
    return 2
  }
  const { values, positionals } = parsed
  const command = positionals[0] ?? null

  if (values.version === true) {
    out(pkg.version)
    return 0
  }
  if (values.help === true || command === "help") {
    process.stdout.write(USAGE)
    return 0
  }
  if (command === null) {
    process.stderr.write(USAGE)
    return 2
  }

  switch (command) {
    case "login":
      return await loginCommand(values.backend)
    case "logout":
      return await logoutCommand()
    case "auth":
      if (positionals[1] === "status" && positionals.length === 2)
        return await authStatusCommand()
      fail("usage: robin auth status")
      return 2
    case "run":
      return await runCommand({
        thread: values.thread,
        model: values.model,
        effort: values.effort,
        prompt: positionals.slice(1).join(" "),
      })
    default:
      fail(`unknown command ${command}`)
      process.stderr.write(USAGE)
      return 2
  }
}

if (import.meta.main) {
  try {
    process.exitCode = await main(process.argv.slice(2))
  } catch (cause) {
    fail(errorMessage(cause))
    process.exitCode = 1
  }
}
