import { HumanMessage, SystemMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import { resolveCodingModel } from "./coding-agent.js"

const BROKER_ENV = {
  OPEN_SWE_OPENAI_OAUTH_BROKER_URL: "http://127.0.0.1:49152/token",
  OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN: "broker-token",
}

function completedResponse(model: string): Response {
  return Response.json({
    id: "resp_test",
    object: "response",
    created_at: 0,
    status: "completed",
    model,
    output: [
      {
        type: "message",
        id: "msg_test",
        status: "completed",
        role: "assistant",
        content: [{ type: "output_text", text: "ok", annotations: [] }],
      },
    ],
    usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 },
  })
}

/**
 * Invoke the resolved model against a fake transport and return the request it
 * put on the wire. Which endpoint and credential that turns out to be is the
 * behaviour under test.
 */
async function captureRequest(
  configurable: Record<string, unknown> = {},
  env: NodeJS.ProcessEnv = BROKER_ENV
): Promise<Request> {
  const captured: Request[] = []
  const fetchImpl: typeof fetch = async (input, init) => {
    const request = new Request(input, init)
    if (request.url.endsWith("/token")) {
      return Response.json({ access_token: "token", account_id: "account" })
    }
    captured.push(request)
    return completedResponse(
      String(configurable.agent_model_id ?? "gpt-5.6-sol")
    )
  }

  const model = resolveCodingModel(configurable, { env, fetchImpl })
  await model
    .invoke([
      new SystemMessage("You are a coding agent."),
      new HumanMessage("List the changes."),
    ])
    .catch(() => undefined)

  const request = captured[0]
  if (!request) throw new Error("The model never issued a request")
  return request
}

describe("resolveCodingModel request", () => {
  it("sends the model and reasoning effort the run selected", async () => {
    const request = await captureRequest({
      agent_model_id: "openai:gpt-5.6-sol",
      agent_effort: "medium",
    })
    const payload = (await request.json()) as Record<string, unknown>

    expect(payload.model).toBe("gpt-5.6-sol")
    expect(payload.reasoning).toMatchObject({ effort: "medium" })
  })
})

describe("credential precedence", () => {
  it("uses the API key and the OpenAI endpoint when OPENAI_API_KEY is set", async () => {
    const request = await captureRequest(
      { agent_model_id: "openai:gpt-5.6-sol" },
      { ...BROKER_ENV, OPENAI_API_KEY: "sk-test" }
    )

    // The ChatGPT session must not capture a run that has a real key: Codex
    // serves a per-account catalog that need not contain the selected model.
    expect(new URL(request.url).host).toBe("api.openai.com")
    expect(request.headers.get("authorization")).toBe("Bearer sk-test")
    expect(request.headers.get("chatgpt-account-id")).toBeNull()
    expect(
      request.headers.get("x-openai-internal-codex-responses-lite")
    ).toBeNull()
  })

  it("honors OPENAI_BASE_URL for the API-key path", async () => {
    const request = await captureRequest(
      {},
      { OPENAI_API_KEY: "sk-test", OPENAI_BASE_URL: "https://proxy.example/v1" }
    )

    expect(request.url).toBe("https://proxy.example/v1/responses")
    expect(request.headers.get("authorization")).toBe("Bearer sk-test")
  })

  it("honors OPENAI_BASE_URL over the Codex default on the OAuth path", async () => {
    const request = await captureRequest(
      {},
      { ...BROKER_ENV, OPENAI_BASE_URL: "https://proxy.example/v1" }
    )

    expect(request.url).toBe("https://proxy.example/v1/responses")
    expect(request.headers.get("authorization")).toBe("Bearer token")
  })

  it("falls back to the ChatGPT session when no key is set", async () => {
    const request = await captureRequest({}, BROKER_ENV)

    // The Codex request shape itself is @open-swe/codex's contract; this only
    // asserts which endpoint and credential the agent selected.
    expect(request.url).toBe("https://chatgpt.com/backend-api/codex/responses")
    expect(request.headers.get("authorization")).toBe("Bearer token")
    expect(request.headers.get("chatgpt-account-id")).toBe("account")
  })
})
