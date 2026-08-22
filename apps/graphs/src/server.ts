import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { startServer } from "@langchain/langgraph-api/server"

import { parseLocalServerOptions } from "./server-options.js"

const options = parseLocalServerOptions(process.argv.slice(2))
const applicationRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  ".."
)
fs.mkdirSync(options.stateDirectory, { recursive: true })
process.chdir(applicationRoot)
process.env.PORT = String(options.port)

const { cleanup } = await startServer({
  host: options.host,
  port: options.port,
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
