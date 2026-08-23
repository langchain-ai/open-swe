/**
 * The whole Open SWE local backend in one process: the LangGraph server on a
 * private loopback port, and the dashboard in front of it on the public one.
 *
 * The desktop app spawns this; running it directly gives the same thing without
 * Electron. Both launch paths execute this file, so there is one runtime to
 * reason about rather than two supervised halves that can drift.
 */

import crypto from "node:crypto"
import fs from "node:fs"
import http from "node:http"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { startServer } from "@langchain/langgraph-api/server"

import { parseAppServerOptions } from "./app-options.js"

function reservePort(host: string): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const probe = http.createServer()
    probe.unref()
    probe.once("error", reject)
    probe.listen(0, host, () => {
      const address = probe.address()
      const port = typeof address === "object" && address ? address.port : null
      probe.close((error) =>
        error || !port ? reject(error ?? new Error("No port")) : resolve(port)
      )
    })
  })
}

const applicationRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  ".."
)
// Running from a checkout, the dashboard build is where the workspace puts it.
const repositoryUi = path.resolve(
  applicationRoot,
  "../../ui/.output/server/index.mjs"
)
const options = parseAppServerOptions(
  process.argv.slice(2),
  process.env,
  process.cwd(),
  fs.existsSync(repositoryUi) ? repositoryUi : undefined
)

fs.mkdirSync(options.stateDirectory, { recursive: true })
process.chdir(applicationRoot)

const graphPort = await reservePort(options.host)
const graphToken = crypto.randomBytes(32).toString("base64url")

const { cleanup } = await startServer({
  host: options.host,
  port: graphPort,
  nWorkers: 10,
  cwd: options.stateDirectory,
  graphs: {
    agent: `${options.graphEntrypoint ?? path.join(applicationRoot, "dist/index.js")}:agent`,
  },
  auth: {
    path: `${path.join(applicationRoot, "dist/auth.js")}:auth`,
    disable_studio_auth: true,
  },
  http: {
    disable_assistants: false,
    disable_threads: false,
    disable_runs: false,
    disable_store: false,
    disable_meta: false,
  },
})

// The dashboard reads these on first request, so they have to be set before its
// entry is imported — importing it is what starts it listening.
process.env.OPEN_SWE_LOCAL_AUTH_TOKEN = graphToken
process.env.OPEN_SWE_LOCAL_GRAPH_ORIGIN = `http://${options.host}:${graphPort}`
process.env.OPEN_SWE_LOCAL_GRAPH_TOKEN = graphToken
process.env.HOST = options.host
process.env.PORT = String(options.port)
process.env.NODE_ENV ||= "production"
if (options.backendUrl) process.env.DASHBOARD_API_URL = options.backendUrl

await import(options.uiEntrypoint)

let closing = false
async function close(): Promise<void> {
  if (closing) return
  closing = true
  await cleanup()
}

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, () => {
    void close().finally(() => process.exit(0))
  })
}
