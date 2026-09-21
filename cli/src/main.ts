import { basename } from "node:path"
import { createInterface } from "node:readline/promises"
import { parseArgs } from "node:util"
import { randomUUID } from "node:crypto"

import pkg from "../package.json"
import { ApiClient, ApiError, normalizeBackend } from "./api.ts"
import { Bridge } from "./bridge.ts"
import {
  clearConfig,
  readBridgeMemory,
  readConfig,
  rememberBridge,
  writeConfig,
} from "./config.ts"
import { isGitRepository, originRepo, repoFullName } from "./git.ts"
import { errorMessage, type JsonObject } from "./json.ts"
import { login } from "./login.ts"
import { renderRunEvents, type RunOutcome } from "./stream.ts"

const USAGE = `open-swe — run a cloud Open SWE agent against this directory

Usage:
  open-swe login [--backend <url>]      Sign in and store the session
  open-swe logout                       Forget the stored session
  open-swe run [options] [prompt...]    Start an agent bridged to this directory
  open-swe --help | --version

Run options:
  --thread <id>    Continue an existing thread
  --model <id>     Agent model id (needs --effort to take effect)
  --effort <name>  Reasoning effort for --model

The agent runs shell commands and reads and writes files in the current
directory, on this machine, without a sandbox.
`

const EVENT_CHANNELS = ["lifecycle", "messages", "tools"] as const
const EVENT_DEPTH = 5

function out(text: string): void {
  process.stdout.write(`${text}\n`)
}

function fail(text: string): void {
  process.stderr.write(`open-swe: ${text}\n`)
}

async function ask(question: string): Promise<string | null> {
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  rl.on("SIGINT", () => {
    rl.close()
    process.emit("SIGINT", "SIGINT")
  })
  try {
    return await rl.question(question)
  } catch {
    return null
  } finally {
    rl.close()
  }
}

async function resolveBackend(explicit: string | undefined): Promise<string> {
  const stored = await readConfig()
  const candidate =
    explicit ?? stored?.backend ?? (await ask("Open SWE backend URL: "))
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

interface RunOptions {
  thread: string | undefined
  model: string | undefined
  effort: string | undefined
  prompt: string
}

async function runCommand(options: RunOptions): Promise<number> {
  const config = await readConfig()
  if (config === null) {
    fail("not signed in — run `open-swe login`")
    return 1
  }
  const root = process.cwd()
  const api = new ApiClient(config.backend, config.session)

  if (!(await isGitRepository(root))) {
    fail(
      `${root} is not a git checkout — the agent will have no repository context`
    )
  }
  out(
    `! The remote agent will run commands and edit files in ${root} on this machine, unsandboxed.`
  )

  const memory = await readBridgeMemory()
  const remembered = memory.roots[root] ?? null
  let rememberedBridgeId = remembered
  if (options.thread !== undefined) {
    const bound = memory.threads[options.thread] ?? null
    if (bound !== null && remembered !== null && bound !== remembered) {
      fail(
        `thread ${options.thread} is bridged to ${bound}, but ${root} is bridged to ${remembered}. ` +
          "Run open-swe from the directory that started that thread."
      )
      return 1
    }
    if (bound !== null) rememberedBridgeId = bound
  }

  const bridge = await Bridge.open(api, {
    rootPath: root,
    label: basename(root),
    rememberedBridgeId,
  })
  out(
    `Bridge ${bridge.session.bridgeId}${bridge.reopened ? " (reopened)" : ""} serving ${root}`
  )

  const creating = options.thread === undefined
  const threadId = options.thread ?? randomUUID()
  await rememberBridge({ root, bridgeId: bridge.session.bridgeId, threadId })
  out(`Thread ${api.dashboardUrl(`/agents/${encodeURIComponent(threadId)}`)}`)

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
    out("\nStopping — press Ctrl-C again to exit immediately.")
    void shutdown()
  })

  bridge.start((error) => {
    fail(errorMessage(error))
    exitCode = 1
    void shutdown()
  })

  const repo = await originRepo(root)
  let prompt = options.prompt
  let first = true

  while (!closing) {
    if (!prompt.trim()) {
      const next = await ask("> ")
      if (next === null || !next.trim()) break
      prompt = next
      continue
    }
    const configurable: JsonObject = {}
    // The bridge, and the repo it belongs to, are stamped on the thread when it
    // is created; the server rejects them on any later run.
    if (creating && first) {
      configurable["sandbox_bridge_id"] = bridge.session.bridgeId
      if (repo !== null) configurable["repo"] = repoFullName(repo)
      else configurable["repo_explicitly_none"] = true
    }
    if (options.model !== undefined) {
      configurable["agent_model_id"] = options.model
      if (options.effort !== undefined)
        configurable["agent_effort"] = options.effort
    }

    let outcome: RunOutcome
    try {
      activeRun = true
      const runId = await api.startRun(threadId, configurable, prompt)
      const response = await api.openEventStream(threadId, {
        channels: EVENT_CHANNELS,
        namespaces: [[]],
        depth: EVENT_DEPTH,
        since: 0,
      })
      outcome = await renderRunEvents(response, runId)
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        fail("your session expired — run `open-swe login` again")
        exitCode = 1
        break
      }
      fail(errorMessage(cause))
      exitCode = 1
      break
    } finally {
      activeRun = false
    }
    if (outcome.status === "failed" && outcome.error !== null) exitCode = 1
    first = false
    prompt = ""
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
