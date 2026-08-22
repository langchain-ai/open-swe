import { describe, expect, it } from "vitest"

import { createOpenAiOAuthFetch, resolveCodingModel } from "./coding-agent.js"

describe("resolveCodingModel", () => {
  it("uses only the supported OpenAI model path", () => {
    const selected = resolveCodingModel({
      agent_model_id: "openai:gpt-test",
      agent_effort: "xhigh",
    })
    const unsupported = resolveCodingModel({
      agent_model_id: "anthropic:claude-test",
      agent_effort: "max",
    })

    expect(selected.model).toBe("gpt-test")
    expect(selected.zdrEnabled).toBe(true)
    expect(selected.modelKwargs).toEqual({
      include: ["reasoning.encrypted_content"],
    })
    expect(unsupported.model).toBe("gpt-5.6-sol")
  })
})

describe("createOpenAiOAuthFetch", () => {
  it("gets short-lived credentials from the loopback broker for each request", async () => {
    const requests: Request[] = []
    const fetchImpl: typeof fetch = async (input, init) => {
      const request = new Request(input, init)
      requests.push(request)
      if (request.url === "http://127.0.0.1:49152/token") {
        return Response.json({
          access_token: "access-token",
          account_id: "example",
        })
      }
      return new Response("ok")
    }
    const oauthFetch = createOpenAiOAuthFetch(
      {
        OPEN_SWE_OPENAI_OAUTH_BROKER_URL: "http://127.0.0.1:49152/token",
        OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN: "broker-token",
      },
      fetchImpl
    )

    const response = await oauthFetch!("https://chatgpt.com/backend-api/codex/responses", {
      method: "POST",
      headers: { authorization: "Bearer oauth" },
      body: "{}",
    })

    expect(await response.text()).toBe("ok")
    expect(requests[0]?.headers.get("authorization")).toBe("Bearer broker-token")
    expect(requests[1]?.headers.get("authorization")).toBe("Bearer access-token")
    expect(requests[1]?.headers.get("chatgpt-account-id")).toBe("example")
  })

  it("rejects a non-loopback credential broker", () => {
    expect(() =>
      createOpenAiOAuthFetch({
        OPEN_SWE_OPENAI_OAUTH_BROKER_URL: "https://example.com/token",
        OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN: "broker-token",
      })
    ).toThrow("credential broker address is invalid")
  })
})
