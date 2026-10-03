import {
  HttpAgent,
  runHttpRequest,
  transformHttpEventStream,
} from "@ag-ui/client"
import {
  AgentRunner,
  CopilotRuntime,
  createCopilotRuntimeHandler,
} from "@copilotkit/runtime/v2"
import type {
  AgentRunnerConnectRequest,
  AgentRunnerRunRequest,
} from "@copilotkit/runtime/v2"
import { COPILOT_RUNTIME_PATH } from "../src/lib/copilotRuntimePath"
import { backendOrigin } from "./backend-proxy"

process.env.COPILOTKIT_TELEMETRY_DISABLED ??= "true"

// Dev serves this handler too, so it takes the dev proxy's default backend.
function apiOrigin(): string {
  return process.env.NODE_ENV === "production"
    ? backendOrigin()
    : (process.env.DASHBOARD_API_URL ?? "http://localhost:2024").replace(
        /\/$/,
        ""
      )
}

const EMPTY_STREAM = () =>
  new Response("", {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  })

/**
 * Stateless: the backend owns run state, so every replica can start a run or
 * replay a thread. Stopping goes through the dashboard's cancel endpoint,
 * because a stop request carries no credentials to forward.
 */
class OpenSweRunner extends AgentRunner {
  run({ agent, input }: AgentRunnerRunRequest) {
    return agent.run(input)
  }

  connect({ threadId, headers }: AgentRunnerConnectRequest) {
    const url = `${apiOrigin()}/dashboard/api/ag-ui/threads/${encodeURIComponent(threadId)}/connect`
    return transformHttpEventStream(
      runHttpRequest(async () => {
        const response = await fetch(url, {
          headers: { ...headers, accept: "text/event-stream" },
        })
        // A thread the client minted but has not started yet has no history.
        return response.status === 404 ? EMPTY_STREAM() : response
      })
    )
  }

  isRunning() {
    return Promise.resolve(false)
  }

  stop() {
    return Promise.resolve(false)
  }
}

const runtime = new CopilotRuntime({
  agents: () => ({
    default: new HttpAgent({ url: `${apiOrigin()}/dashboard/api/ag-ui/run` }),
  }),
  runner: new OpenSweRunner(),
  forwardHeaders: { allow: ["cookie", "origin", "referer"] },
})

const handler = createCopilotRuntimeHandler({
  runtime,
  basePath: COPILOT_RUNTIME_PATH,
})

export default function copilotRuntime(event: { req: Request }) {
  return handler(event.req)
}
