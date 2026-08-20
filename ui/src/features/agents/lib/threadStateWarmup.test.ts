/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import { threadStateWarmupScript } from "./threadStateWarmup"

const THREAD_ID = "1dd69115-f4b9-507f-b4d5-9f355f9f5ba0"
const STATE_PATH = `/dashboard/api/threads/${THREAD_ID}/state`

function setReadyState(value: DocumentReadyState) {
  Object.defineProperty(document, "readyState", {
    value,
    configurable: true,
  })
}

function run(script: string) {
  new Function(script)()
}

afterEach(() => {
  vi.unstubAllGlobals()
  setReadyState("complete")
})

describe("threadStateWarmupScript", () => {
  it("only matches a cloud thread route", () => {
    expect(threadStateWarmupScript("/agents")).toBeNull()
    expect(threadStateWarmupScript("/agents/threads")).toBeNull()
    expect(threadStateWarmupScript("/agents/skills")).toBeNull()
    expect(threadStateWarmupScript(`/agents/local/${THREAD_ID}`)).toBeNull()
    expect(threadStateWarmupScript(`/agents/${THREAD_ID}/plan`)).toBeNull()
    expect(threadStateWarmupScript(`/agents/${THREAD_ID}`)).toContain(
      STATE_PATH
    )
  })

  it("issues the state request during parse and hands it to the first matching fetch", async () => {
    setReadyState("loading")
    const response = new Response("{}")
    const original = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(response)
    )
    vi.stubGlobal("fetch", original)

    run(threadStateWarmupScript(`/agents/${THREAD_ID}`)!)

    expect(original).toHaveBeenCalledTimes(1)
    expect(String(original.mock.calls[0]?.[0])).toContain(STATE_PATH)

    const handed = await window.fetch(new URL(STATE_PATH, location.href).href, {
      credentials: "include",
    })
    expect(handed).toBe(response)
    expect(original).toHaveBeenCalledTimes(1)

    await window.fetch(new URL(STATE_PATH, location.href).href)
    expect(original).toHaveBeenCalledTimes(2)
    expect(window.fetch).toBe(original)
  })

  it("passes unrelated requests through and restores fetch", async () => {
    setReadyState("loading")
    const original = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response("{}"))
    )
    vi.stubGlobal("fetch", original)

    run(threadStateWarmupScript(`/agents/${THREAD_ID}`)!)
    await window.fetch("/dashboard/api/options")

    expect(original).toHaveBeenCalledTimes(2)
    expect(String(original.mock.calls[1]?.[0])).toBe("/dashboard/api/options")
  })

  it("is inert once the document has parsed", () => {
    setReadyState("complete")
    const original = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response("{}"))
    )
    vi.stubGlobal("fetch", original)

    run(threadStateWarmupScript(`/agents/${THREAD_ID}`)!)

    expect(original).not.toHaveBeenCalled()
    expect(window.fetch).toBe(original)
  })
})
