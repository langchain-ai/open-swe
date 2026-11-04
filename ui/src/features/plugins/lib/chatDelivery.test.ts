import { afterEach, expect, it, vi } from "vitest"

import { pluginsApi } from "@/features/plugins/lib/api"
import { PluginChatDeliveries } from "@/features/plugins/lib/chatDelivery"

afterEach(() => vi.restoreAllMocks())

it("keeps a failed result with its chat and retries it without running the tool again", async () => {
  const calls = {
    first: [{ call_id: "first-call", tool: "edit", arguments: {} }],
    second: [{ call_id: "second-call", tool: "edit", arguments: {} }],
  }
  vi.spyOn(pluginsApi, "claimCalls").mockImplementation(
    async (_plugin, chat) => (chat === "first" ? calls.first : calls.second)
  )
  const send = vi
    .spyOn(pluginsApi, "answerCalls")
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce({ run_id: null, paused: true })
    .mockResolvedValue({ run_id: "resumed", paused: false })
  const tools = {
    offer: {
      tools: [
        { name: "edit", description: "", parameters: { type: "object" } },
      ],
      instructions: "",
    },
    run: vi.fn(async () => "saved"),
  }
  const deliveries = new PluginChatDeliveries("install")

  await expect(deliveries.answer("first", tools, "Docs")).rejects.toThrow(
    "offline"
  )
  await deliveries.answer("second", tools, "Docs")
  expect(deliveries.hasUndelivered("first")).toBe(true)
  expect(deliveries.isPaused("first")).toBe(false)
  expect(deliveries.isPaused("second")).toBe(true)

  await deliveries.answer("first", tools, "Docs")
  expect(tools.run).toHaveBeenCalledTimes(2)
  expect(send.mock.calls.map(([, chat, outputs]) => [chat, outputs])).toEqual([
    ["first", [{ call_id: "first-call", output: "saved" }]],
    ["second", [{ call_id: "second-call", output: "saved" }]],
    ["first", [{ call_id: "first-call", output: "saved" }]],
  ])
  expect(deliveries.hasUndelivered("first")).toBe(false)
  expect(deliveries.isPaused("second")).toBe(true)
  calls.first = []
  await deliveries.answer("first", tools, "Docs")
  expect(send).toHaveBeenCalledTimes(3)
})

it("returns an in-flight tool result to its original chat", async () => {
  vi.spyOn(pluginsApi, "claimCalls").mockImplementation(
    async (_plugin, chat) => [
      { call_id: `${chat}-call`, tool: "edit", arguments: {} },
    ]
  )
  const send = vi
    .spyOn(pluginsApi, "answerCalls")
    .mockResolvedValue({ run_id: "resumed", paused: false })
  let finish = (_value: string) => {}
  const pending = new Promise<string>((resolve) => {
    finish = resolve
  })
  const tools = {
    offer: {
      tools: [
        { name: "edit", description: "", parameters: { type: "object" } },
      ],
      instructions: "",
    },
    run: vi
      .fn()
      .mockImplementationOnce(() => pending)
      .mockResolvedValue("second"),
  }
  const deliveries = new PluginChatDeliveries("install")
  const first = deliveries.answer("first", tools, "Docs")
  await vi.waitFor(() => expect(tools.run).toHaveBeenCalledTimes(1))
  await deliveries.answer("second", tools, "Docs")
  finish("first")
  await first

  expect(send.mock.calls.map(([, chat, outputs]) => [chat, outputs])).toEqual([
    ["second", [{ call_id: "second-call", output: "second" }]],
    ["first", [{ call_id: "first-call", output: "first" }]],
  ])
})
