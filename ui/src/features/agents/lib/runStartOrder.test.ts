import { expect, it, vi } from "vitest"
import { orderRunStarts } from "./runStartOrder"

const THREAD = "http://localhost/dashboard/api/threads/one"

it.each(["/runs/stream", "/runs"])(
  "sends a thread's run starts on %s once the server has answered the one before",
  async (route) => {
    let answer: (response: Response) => void = () => {}
    const fetcher = vi
      .fn<typeof fetch>()
      .mockReturnValueOnce(new Promise((resolve) => (answer = resolve)))
      .mockResolvedValue(Response.json({}))
    const ordered = orderRunStarts(fetcher)
    const post = { method: "POST", body: "{}" }

    const first = ordered(`${THREAD}${route}`, post)
    const second = ordered(`${THREAD}${route}`, post)
    await Promise.resolve()
    expect(fetcher).toHaveBeenCalledOnce()

    answer(Response.json({}))
    await Promise.all([first, second])
    expect(fetcher).toHaveBeenCalledTimes(2)
  }
)

it("never holds a request that starts no run", async () => {
  const fetcher = vi.fn<typeof fetch>((input) =>
    String(input).endsWith("/runs/stream")
      ? new Promise<Response>(() => {})
      : Promise.resolve(Response.json({}))
  )
  const ordered = orderRunStarts(fetcher)

  void ordered(`${THREAD}/runs/stream`, { method: "POST", body: "{}" })
  await ordered(`${THREAD}/runs/run-1/cancel`, { method: "POST", body: "{}" })
  await ordered(`${THREAD}/runs`, { method: "GET" })

  expect(fetcher).toHaveBeenCalledTimes(3)
})
