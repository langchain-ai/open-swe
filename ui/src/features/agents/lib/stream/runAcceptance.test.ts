import { expect, it, vi } from "vitest"
import { createRunAcceptanceTracker } from "./runAcceptance"

it("isolates overlapping submissions and ignores untracked requests", async () => {
  const fetcher = vi.fn<typeof fetch>()
  const tracker = createRunAcceptanceTracker(fetcher)
  const send = (id: string) =>
    tracker.fetch("http://localhost/threads/one/commands", {
      method: "POST",
      body: JSON.stringify({
        method: "run.start",
        params: { input: { messages: [{ id }] } },
      }),
    })
  const success = () =>
    Response.json({ type: "success", result: { run_id: "run" } })
  fetcher.mockResolvedValueOnce(success())
  await send("untracked")
  expect(await tracker.track("untracked")()).toBe(false)
  const first = tracker.track("first")
  const second = tracker.track("second")
  fetcher
    .mockResolvedValueOnce(success())
    .mockResolvedValueOnce(Response.json({ type: "error" }))
  await Promise.all([send("first"), send("second")])
  expect(await first()).toBe(true)
  expect(await second()).toBe(false)
  expect(await tracker.track("first")()).toBe(false)
})

it.each([202, 204, 503])(
  "does not treat an HTTP %s response as run creation",
  async (status) => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status }))
    const tracker = createRunAcceptanceTracker(fetcher)
    const accepted = tracker.track("message")
    await tracker.fetch("http://localhost/threads/one/runs", {
      method: "POST",
      body: JSON.stringify({ input: { messages: [{ id: "message" }] } }),
    })
    expect(await accepted()).toBe(false)
  }
)
