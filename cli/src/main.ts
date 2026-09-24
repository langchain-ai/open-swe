import { basename } from "node:path"
import { parseArgs } from "node:util"
import { randomUUID } from "node:crypto"

import pkg from "../package.json"
import { ApiClient, ApiError, normalizeBackend } from "./api.ts"
import { Bridge } from "./bridge.ts"
import {
  clearConfig,
  readBridgeMemory,
  readBackend,
  readConfig,
  rememberThreadBridge,
  writeConfig,
} from "./config.ts"
import { isGitRepository, originRepo, repoFullName } from "./git.ts"
import { composePrompt, readPipedStdin } from "./input.ts"
import { errorMessage, type JsonObject } from "./json.ts"
import { login } from "./login.ts"
import {
  collectRun,
  RESULT_TOOL,
  type CliResult,
  type RunOutcome,
} from "./stream.ts"

const USAGE = `oswe — run a cloud Open SWE agent against this directory

Usage:
  oswe login [--backend <url>]      Sign in and store the session
  oswe logout                       Forget the stored session
  oswe run [options] [prompt...]    Start an agent bridged to this directory
  oswe --help | --version

Run options:
  --thread <id>    Continue an existing thread
  --model <id>     Agent model id (needs --effort to take effect)
  --effort <name>  Reasoning effort for --model
  --visibility <v> workspace (default) or private; API keys and CI always
                   start system threads

Piped input is attached below the prompt, or is the prompt when none is given.
Stdout carries only the result the agent reports. The exit code is the one it
reports: 0 done or yes, 1 failed or no, 2 could not tell.

The agent runs shell commands and reads and writes files in the current
directory, on this machine, without a sandbox.
`

const EVENT_CHANNELS = ["lifecycle", "tools"] as const
const EVENT_DEPTH = 5

function out(text: string): void {
  process.stdout.write(`${text}\n`)
}

/** Stdout carries only the agent's result, so everything the CLI says goes here. */
function note(text: string): void {
  process.stderr.write(`${text}\n`)
}

function fail(text: string): void {
  process.stderr.write(`oswe: ${text}\n`)
}

function printResult(result: CliResult): void {
  const { stdout } = result
  process.stdout.write(
    stdout === "" || stdout.endsWith("\n") ? stdout : `${stdout}\n`
  )
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
  const path = await writeConfig({ backend, session })
  out(`Signed in to ${backend}. Session stored in ${path}.`)
  return 0
}

async function logoutCommand(): Promise<number> {
  out((await clearConfig()) ? "Signed out." : "No stored session.")
  return 0
}

const VISIBILITIES = ["workspace", "private"] as const
type Visibility = (typeof VISIBILITIES)[number]

function isVisibility(value: string): value is Visibility {
  return (VISIBILITIES as readonly string[]).includes(value)
}

interface RunOptions {
  thread: string | undefined
  visibility: string | undefined
  model: string | undefined
  effort: string | undefined
  prompt: string
}

async function runCommand(options: RunOptions): Promise<number> {
  const prompt = composePrompt(options.prompt, await readPipedStdin())
  if (!prompt) {
    fail("a prompt is required, as arguments or on stdin")
    return 2
  }
  const config = await readConfig()
  if (config === null) {
    fail("not signed in — run `oswe login`, or set OPEN_SWE_API_KEY")
    return 1
  }
  const { credential } = config
  let visibility: Visibility = "workspace"
  if (options.visibility !== undefined) {
    if (!isVisibility(options.visibility)) {
      fail("--visibility must be workspace or private")
      return 2
    }
    if (credential.machine) {
      fail("API keys and CI start system threads; --visibility does not apply")
      return 2
    }
    visibility = options.visibility
  }
  const threadType = credential.machine ? "system" : visibility
  const root = process.cwd()
  const api = new ApiClient(config.backend, credential)

  if (!(await isGitRepository(root))) {
    fail(
      `${root} is not a git checkout — the agent will have no repository context`
    )
  }
  note(
    `! The remote agent will run commands and edit files in ${root} on this machine, unsandboxed.`
  )

  let bridgeId: string | null = null
  if (options.thread !== undefined) {
    const bound = (await readBridgeMemory())[options.thread]
    if (bound === undefined) {
      fail(`thread ${options.thread} was not started by oswe on this machine`)
      return 1
    }
    if (bound.root !== root) {
      fail(`thread ${options.thread} serves ${bound.root}; run oswe from there`)
      return 1
    }
    bridgeId = bound.bridgeId
  }

  let bridge: Bridge
  try {
    bridge = await Bridge.open(api, {
      rootPath: root,
      label: basename(root),
      bridgeId,
    })
  } catch (cause) {
    if (!(cause instanceof ApiError)) throw cause
    if (cause.status === 401) fail(credential.rejected)
    else if (cause.status === 404)
      fail(`the bridge thread ${options.thread} was bound to no longer exists`)
    else if (cause.status === 409)
      fail(`another oswe process is serving thread ${options.thread}`)
    else throw cause
    return 1
  }
  note(
    `Bridge ${bridge.session.bridgeId}${bridge.reopened ? " (reopened)" : ""} serving ${root}`
  )

  const creating = options.thread === undefined
  const threadId = options.thread ?? randomUUID()
  await rememberThreadBridge(threadId, {
    bridgeId: bridge.session.bridgeId,
    root,
  })
  note(`Thread ${api.dashboardUrl(`/agents/${encodeURIComponent(threadId)}`)}`)

  let exitCode = 0
  let closing = false
  let interrupts = 0
  let activeRun = false

  const shutdown = async (): Promise<void> => {
    if (closing) return
    closing = true
    if (activeRun) {
      try {
        await api.cancelThread(threadId)
      } catch (cause) {
        fail(`could not cancel the run: ${errorMessage(cause)}`)
      }
    }
    await bridge.close()
    process.exit(exitCode)
  }

  process.on("SIGINT", () => {
    interrupts += 1
    if (interrupts > 1) process.exit(130)
    note("\nStopping — press Ctrl-C again to exit immediately.")
    void shutdown()
  })

  bridge.start((error) => {
    fail(errorMessage(error))
    exitCode = 1
    void shutdown()
  })

  const repo = await originRepo(root)
  // Every machine run names its thread type, a resumed one included.
  const configurable: JsonObject = { thread_type: threadType }
  // The bridge, and the repo it belongs to, are stamped on the thread when it
  // is created; the server rejects them on any later run.
  if (creating) {
    configurable["sandbox_bridge_id"] = bridge.session.bridgeId
    if (repo !== null) configurable["repo"] = repoFullName(repo)
    else configurable["repo_explicitly_none"] = true
  }
  if (options.model !== undefined) {
    configurable["agent_model_id"] = options.model
    if (options.effort !== undefined)
      configurable["agent_effort"] = options.effort
  }

  let outcome: RunOutcome | null = null
  try {
    activeRun = true
    const runId = await api.startRun(threadId, configurable, prompt)
    const response = await api.openEventStream(threadId, {
      channels: EVENT_CHANNELS,
      namespaces: [[]],
      depth: EVENT_DEPTH,
      since: 0,
    })
    outcome = await collectRun(response, runId)
  } catch (cause) {
    fail(
      cause instanceof ApiError && cause.status === 401
        ? credential.rejected
        : errorMessage(cause)
    )
    exitCode = 1
  } finally {
    activeRun = false
  }

  if (outcome?.status === "completed" && outcome.result) {
    printResult(outcome.result)
    exitCode = outcome.result.exitCode
  } else if (outcome !== null) {
    fail(
      outcome.status === "completed"
        ? `the agent finished without calling ${RESULT_TOOL}`
        : `run ${outcome.status}${outcome.error ? `: ${outcome.error}` : ""}`
    )
    exitCode = 1
  }

  if (!closing) {
    closing = true
    await bridge.close()
  }
  return exitCode
}

const CLI_OPTIONS = {
  backend: { type: "string" },
  thread: { type: "string" },
  visibility: { type: "string" },
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
    case "run":
      return await runCommand({
        thread: values.thread,
        visibility: values.visibility,
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
