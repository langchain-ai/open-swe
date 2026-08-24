import { describe, expect, it } from "vitest"

import { createOpenAiOAuthFetch } from "./oauth.js"

const BROKER_ENV = {
  OPEN_SWE_OPENAI_OAUTH_BROKER_URL: "http://127.0.0.1:49152/token",
  OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN: "broker-token",
}

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
    const oauthFetch = createOpenAiOAuthFetch(BROKER_ENV, fetchImpl)

    const response = await oauthFetch!(
      "https://chatgpt.com/backend-api/codex/responses",
      { method: "POST", headers: { authorization: "Bearer oauth" }, body: "{}" }
    )

    expect(await response.text()).toBe("ok")
    expect(requests[0]?.headers.get("authorization")).toBe(
      "Bearer broker-token"
    )
    expect(requests[1]?.headers.get("authorization")).toBe(
      "Bearer access-token"
    )
    expect(requests[1]?.headers.get("chatgpt-account-id")).toBe("example")
    expect(requests[1]?.headers.get("originator")).toBe("langchain")
  })

  it("applies the Codex request shape to a responses payload", async () => {
    const sent: Request[] = []
    const fetchImpl: typeof fetch = async (input, init) => {
      const request = new Request(input, init)
      if (request.url.endsWith("/token")) {
        return Response.json({ access_token: "t", account_id: "a" })
      }
      sent.push(request)
      return new Response("ok")
    }
    const oauthFetch = createOpenAiOAuthFetch(BROKER_ENV, fetchImpl)

    await oauthFetch!("https://chatgpt.com/backend-api/codex/responses", {
      method: "POST",
      body: JSON.stringify({
        model: "gpt-5.6-sol",
        input: [
          { role: "developer", content: "Be careful." },
          { role: "user", content: "Go." },
        ],
      }),
    })

    const request = sent[0]!
    expect(request.headers.get("x-openai-internal-codex-responses-lite")).toBe(
      "true"
    )
    const payload = (await request.json()) as Record<string, unknown>
    expect(payload.instructions).toBe("Be careful.")
    expect(payload.input).toEqual([{ role: "user", content: "Go." }])
  })

  it("leaves a non-JSON body untouched", async () => {
    const sent: Request[] = []
    const fetchImpl: typeof fetch = async (input, init) => {
      const request = new Request(input, init)
      if (request.url.endsWith("/token")) {
        return Response.json({ access_token: "t", account_id: "a" })
      }
      sent.push(request)
      return new Response("ok")
    }
    const oauthFetch = createOpenAiOAuthFetch(BROKER_ENV, fetchImpl)

    await oauthFetch!("https://chatgpt.com/backend-api/codex/responses", {
      method: "POST",
      body: "not json",
    })

    expect(sent[0]?.headers.get("x-openai-internal-codex-responses-lite")).toBe(
      null
    )
    expect(await sent[0]!.text()).toBe("not json")
  })

  it("returns null when no broker is configured", () => {
    expect(createOpenAiOAuthFetch({})).toBe(null)
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
