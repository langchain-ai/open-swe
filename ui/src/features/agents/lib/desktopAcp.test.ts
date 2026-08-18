/** @vitest-environment jsdom */

import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useDesktopAcpSession, useDesktopAcpSessions } from "./desktopAcp"
import { desktopAcpMessages } from "./desktopAcpMessages"
import type {
  DesktopAcpEvent,
  DesktopAcpSession,
  DesktopAcpSessionSummary,
} from "@/desktop"

afterEach(() => {
  cleanup()
  delete window.openSweDesktop
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}

function session(
  id: string,
  status: DesktopAcpSession["status"] = "idle",
  events: Array<DesktopAcpEvent> = []
): DesktopAcpSession {
  return {
    id,
    cwd: `/tmp/${id}`,
    title: id,
    status,
    createdAt: 1,
    updatedAt: 1,
    events,
  }
}

function installDesktop(
  getAcpSession: (id: string) => Promise<DesktopAcpSession | null>,
  onEvent: (
    callback: (payload: {
      sessionId: string
      event: DesktopAcpEvent
      session: DesktopAcpSessionSummary
    }) => void
  ) => () => void
) {
  window.openSweDesktop = {
    getAcpSession,
    onAcpEvent: onEvent,
  } as Window["openSweDesktop"]
}

describe("desktopAcpMessages", () => {
  it("parses structured desktop messages and hides entity introductions", () => {
    const messages = desktopAcpMessages([
      {
        sequence: 0,
        timestamp: "2026-08-05T20:00:00Z",
        type: "user-message",
        text: '<chat_entity kind="system" id="system:local"><display_name>Local automation</display_name></chat_entity>',
        images: [],
      },
      {
        sequence: 1,
        timestamp: "2026-08-05T20:00:01Z",
        type: "user-message",
        text: '<chat_message sender="system:local" surface="desktop"><content>Run &lt;safe&gt;</content></chat_message>',
        images: [],
      },
      {
        sequence: 2,
        timestamp: "2026-08-05T20:00:02Z",
        type: "user-message",
        text: "Legacy desktop prompt",
        images: [],
      },
    ])

    expect(messages).toHaveLength(2)
    expect(messages[0]).toMatchObject({
      author: "system",
      structuredSenderKind: "system",
      structuredSenderName: "Local automation",
      chunks: [{ kind: "text", text: "Run <safe>" }],
    })
    expect(messages[1]).toMatchObject({
      author: "user",
      chunks: [{ kind: "text", text: "Legacy desktop prompt" }],
    })
  })

  it("builds a local conversation and updates tool calls in place", () => {
    const messages = desktopAcpMessages([
      {
        sequence: 0,
        timestamp: "2026-08-05T20:00:00Z",
        type: "user-message",
        text: "Fix it",
        images: [],
      },
      {
        sequence: 1,
        timestamp: "2026-08-05T20:00:01Z",
        type: "agent-text",
        text: "I’ll inspect it.",
      },
      {
        sequence: 2,
        timestamp: "2026-08-05T20:00:02Z",
        type: "tool",
        tool: {
          toolCallId: "tool-1",
          title: "Read file",
          toolKind: "read",
          status: "in_progress",
        },
      },
      {
        sequence: 3,
        timestamp: "2026-08-05T20:00:03Z",
        type: "tool",
        tool: {
          toolCallId: "tool-1",
          title: "Read file",
          toolKind: "read",
          status: "completed",
          output: "done",
        },
      },
    ])

    expect(messages).toHaveLength(2)
    expect(messages[0]?.author).toBe("user")
    expect(messages[1]?.chunks).toEqual([
      { kind: "text", text: "I’ll inspect it." },
      {
        kind: "tool-execution",
        toolCallId: "tool-1",
        title: "Read file",
        toolKind: "read",
        input: {},
        status: "completed",
        output: "done",
        locations: undefined,
      },
    ])
  })
})

describe("useDesktopAcpSession", () => {
  it("does not expose a previous session or accept its late hydration", async () => {
    const first = deferred<DesktopAcpSession | null>()
    const second = deferred<DesktopAcpSession | null>()
    installDesktop(
      (id) => (id === "first" ? first.promise : second.promise),
      () => vi.fn()
    )

    const { result, rerender } = renderHook(
      ({ id }) => useDesktopAcpSession(id),
      { initialProps: { id: "first" } }
    )
    rerender({ id: "second" })

    expect(result.current.session).toBeNull()
    expect(result.current.loaded).toBe(false)

    act(() => second.resolve(session("second")))
    await waitFor(() => expect(result.current.session?.id).toBe("second"))
    await act(async () => {
      first.resolve(session("first"))
      await first.promise
    })

    expect(result.current.session?.id).toBe("second")
  })

  it("applies terminal events received during hydration", async () => {
    const hydration = deferred<DesktopAcpSession | null>()
    let emit!: (payload: {
      sessionId: string
      event: DesktopAcpEvent
      session: DesktopAcpSessionSummary
    }) => void
    installDesktop(
      () => hydration.promise,
      (callback) => {
        emit = callback
        return vi.fn()
      }
    )

    const { result } = renderHook(() => useDesktopAcpSession("local"))
    const runEnd: DesktopAcpEvent = {
      sequence: 1,
      timestamp: "2026-08-05T20:00:01Z",
      type: "run-end",
    }
    act(() =>
      emit({
        sessionId: "local",
        event: runEnd,
        session: session("local", "idle"),
      })
    )
    act(() =>
      hydration.resolve(
        session("local", "running", [
          {
            sequence: 0,
            timestamp: "2026-08-05T20:00:00Z",
            type: "run-start",
          },
        ])
      )
    )

    await waitFor(() => expect(result.current.session?.status).toBe("idle"))
    expect(result.current.session?.events).toHaveLength(2)
  })
})

describe("useDesktopAcpSessions", () => {
  it("does not restore a deleted session from stale events or listing", async () => {
    const listing = deferred<Array<DesktopAcpSessionSummary>>()
    let emit!: (payload: {
      sessionId: string
      event: DesktopAcpEvent
      session: DesktopAcpSessionSummary
    }) => void
    window.openSweDesktop = {
      listAcpSessions: () => listing.promise,
      deleteAcpSession: () => Promise.resolve(true),
      onAcpEvent: (callback: typeof emit) => {
        emit = callback
        return vi.fn()
      },
    } as Partial<
      NonNullable<Window["openSweDesktop"]>
    > as Window["openSweDesktop"]
    const { result } = renderHook(() => useDesktopAcpSessions())
    const summary = session("local")
    const event: DesktopAcpEvent = {
      sequence: 0,
      timestamp: "2026-08-05T20:00:00Z",
      type: "run-end",
    }

    act(() => emit({ sessionId: "local", event, session: summary }))
    await waitFor(() => expect(result.current.sessions).toHaveLength(1))
    await act(async () => {
      expect(await result.current.deleteSession("local")).toBe(true)
    })
    act(() => emit({ sessionId: "local", event, session: summary }))
    await act(async () => {
      listing.resolve([summary])
      await listing.promise
    })

    expect(result.current.sessions).toEqual([])
  })
})
