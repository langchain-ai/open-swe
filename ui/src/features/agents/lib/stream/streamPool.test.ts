import { beforeEach, describe, expect, it } from "vitest"

import {
  IDLE_STREAM_TTL_MS,
  MAX_IDLE_STREAMS,
  useStreamPool,
} from "./streamPool"
import type { AgentStream } from "./streamPool"

const NOW = 1_000_000

function handle(isLoading: boolean): AgentStream {
  return { isLoading } as unknown as AgentStream
}

function pool() {
  return useStreamPool.getState()
}

function activeEntry() {
  const state = pool()
  return state.entries.find((entry) => entry.id === state.activeId)
}

beforeEach(() => {
  useStreamPool.setState({
    entries: [],
    handles: {},
    activeId: null,
    binding: null,
    createdThreadId: null,
  })
})

describe("streamPool", () => {
  it("reuses the retained instance when returning to a thread", () => {
    pool().activate("cloud", "one")
    const first = activeEntry()
    pool().activate("cloud", "two")
    pool().activate("cloud", "one")

    expect(activeEntry()?.id).toBe(first?.id)
    expect(pool().entries).toHaveLength(2)
  })

  it("keeps cloud and local instances of the same id apart", () => {
    pool().activate("cloud", "same")
    pool().activate("local", "same")

    expect(pool().entries.map((entry) => entry.transport)).toEqual([
      "cloud",
      "local",
    ])
  })

  it("mounts a fresh instance for every visit to the home page", () => {
    pool().activate("cloud", null)
    const first = activeEntry()
    pool().activate("cloud", "one")
    pool().activate("cloud", null)

    expect(activeEntry()?.id).not.toBe(first?.id)
  })

  it("follows a lazily created thread to its server id", () => {
    pool().activate("cloud", null)
    const draft = activeEntry()
    if (!draft) throw new Error("no active entry")
    pool().rekey(draft.id, "minted")
    pool().activate("cloud", "minted")

    expect(activeEntry()?.id).toBe(draft.id)
    expect(activeEntry()?.threadId).toBe("minted")
  })

  it("kicks a thread by bumping its generation without dropping the handle", () => {
    pool().activate("cloud", "stuck")
    const entry = activeEntry()
    if (!entry) throw new Error("no active entry")
    pool().publish(entry.id, handle(true))

    pool().kick("cloud", "stuck")

    expect(activeEntry()?.generation).toBe(entry.generation + 1)
    expect(pool().handles[entry.id]).toBeDefined()
  })

  it("drops idle instances after the TTL but never a running one", () => {
    pool().activate("cloud", "running")
    const running = activeEntry()
    if (!running) throw new Error("no active entry")
    pool().publish(running.id, handle(true))
    pool().activate("cloud", "idle")
    pool().activate("cloud", "current")
    useStreamPool.setState((state) => ({
      entries: state.entries.map((entry) =>
        entry.threadId === "current"
          ? entry
          : { ...entry, lastActiveAt: NOW - IDLE_STREAM_TTL_MS }
      ),
    }))

    pool().sweep(NOW)

    expect(pool().entries.map((entry) => entry.threadId)).toEqual([
      "running",
      "current",
    ])
    expect(pool().handles[running.id]).toBeDefined()
  })

  it("restarts the retention window when a thread is left", () => {
    pool().activate("cloud", "stale")
    useStreamPool.setState((state) => ({
      entries: state.entries.map((entry) => ({
        ...entry,
        lastActiveAt: Date.now() - 2 * IDLE_STREAM_TTL_MS,
      })),
    }))

    pool().activate("cloud", "next")

    expect(pool().entries.map((entry) => entry.threadId)).toEqual([
      "stale",
      "next",
    ])
  })

  it("caps retained idle instances, evicting the least recently active", () => {
    for (let index = 0; index <= MAX_IDLE_STREAMS; index += 1) {
      pool().activate("cloud", `thread-${index}`)
    }
    pool().activate("cloud", "current")
    useStreamPool.setState((state) => ({
      entries: state.entries.map((entry, index) =>
        entry.threadId === "current"
          ? entry
          : { ...entry, lastActiveAt: NOW + index }
      ),
    }))

    pool().sweep(NOW + MAX_IDLE_STREAMS + 1)

    const retained = pool().entries.map((entry) => entry.threadId)
    expect(retained).not.toContain("thread-0")
    expect(retained).toContain(`thread-${MAX_IDLE_STREAMS}`)
    expect(retained).toContain("current")
    expect(retained).toHaveLength(MAX_IDLE_STREAMS + 1)
  })
})
