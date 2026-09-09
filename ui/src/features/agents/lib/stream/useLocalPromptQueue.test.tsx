/** @vitest-environment jsdom */

import { act, renderHook, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { useLocalPromptQueue } from "./useLocalPromptQueue"
import type { Client } from "@langchain/langgraph-sdk"

function fakeClient() {
  const items = new Map<string, { value: Record<string, unknown> }>()
  const key = (namespace: Array<string>, name: string) =>
    [...namespace, name].join("/")
  return {
    items,
    client: {
      store: {
        getItem: async (namespace: Array<string>, name: string) =>
          items.get(key(namespace, name)) ?? null,
        putItem: async (
          namespace: Array<string>,
          name: string,
          value: Record<string, unknown>
        ) => {
          items.set(key(namespace, name), { value })
        },
        deleteItem: async (namespace: Array<string>, name: string) => {
          items.delete(key(namespace, name))
        },
      },
    } as unknown as Client,
  }
}

function setup(isRunning: boolean) {
  const { client, items } = fakeClient()
  const submit = vi.fn(async () => true)
  const hook = renderHook(
    (props: { isRunning: boolean }) =>
      useLocalPromptQueue({
        client,
        sessionId: "session-1",
        login: "alice",
        isRunning: props.isRunning,
        submit,
      }),
    { initialProps: { isRunning } }
  )
  return { ...hook, items, submit }
}

describe("useLocalPromptQueue", () => {
  it("sends what the user queued once the run they stopped is gone", async () => {
    const { result, rerender, submit } = setup(true)

    await act(() => result.current.enqueue("pick this up next", []))
    expect(result.current.queued.map((item) => item.content)).toEqual([
      "pick this up next",
    ])
    expect(submit).not.toHaveBeenCalled()

    rerender({ isRunning: false })

    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith("pick this up next", [])
    )
    expect(result.current.queued).toEqual([])
  })

  it("does not resend a follow-up the agent already drained", async () => {
    const { result, rerender, submit, items } = setup(true)

    await act(() => result.current.enqueue("already handled", []))
    items.clear()
    rerender({ isRunning: false })

    await waitFor(() => expect(result.current.queued).toEqual([]))
    expect(submit).not.toHaveBeenCalled()
  })

  it("merges several undrained follow-ups into one run", async () => {
    const { result, rerender, submit } = setup(true)

    await act(() => result.current.enqueue("first", []))
    await act(() => result.current.enqueue("second", []))
    rerender({ isRunning: false })

    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith("first\n\nsecond", [])
    )
  })
})
