/** @vitest-environment jsdom */

import { expect, it, vi } from "vitest"

import { initializeDatadogRum } from "@/lib/datadog"
import { installDatadogPerfSink } from "./datadogSink"
import { startSpan } from "./trace"

it("delivers early list loads and later individual thread loads to RUM once", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ environment: "test" }))
  )
  const addDurationVital = vi.fn()
  const rum = {
    init: vi.fn(),
    getInternalContext: () => undefined,
    addDurationVital,
    setGlobalContextProperty: vi.fn(),
  }
  installDatadogPerfSink()
  startSpan("thread_list_load", { scope: "recents" }).end({ items: 2 })
  startSpan("thread_list_load").abandon("unmounted")
  expect(addDurationVital).not.toHaveBeenCalled()
  await initializeDatadogRum(
    { VITE_DATADOG_APPLICATION_ID: "app", VITE_DATADOG_CLIENT_TOKEN: "token" },
    async () => rum
  )
  startSpan("thread_load").end({ messages: 3 })
  expect(addDurationVital.mock.calls.map(([name]) => name)).toEqual([
    "thread_list_load",
    "thread_load",
  ])
  expect(addDurationVital.mock.calls[0]?.[1]).toMatchObject({
    context: { scope: "recents", items: 2 },
  })
  expect(rum.setGlobalContextProperty).toHaveBeenCalledWith("client", "web")
  vi.unstubAllGlobals()
})
