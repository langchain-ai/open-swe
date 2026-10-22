/** @vitest-environment jsdom */

import { renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { AgentThread } from "@/features/agents/lib/types"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"
import {
  NOTIFICATIONS_PREF_KEY,
  runNotificationContent,
  showRunNotification,
} from "@/lib/notifications"

class FakeNotification {
  static permission: NotificationPermission = "granted"
  static instances: Array<FakeNotification> = []

  onclick: (() => void) | null = null
  close = vi.fn()

  constructor(
    readonly title: string,
    readonly options?: NotificationOptions
  ) {
    FakeNotification.instances.push(this)
  }
}

const thread: AgentThread = {
  id: "thread-123",
  title: "Fix notifications",
  repo: "open-swe",
  repoFullName: "langchain-ai/open-swe",
  branch: "main",
  model: "test-model",
  status: "finished",
  viewed: false,
  createdAt: 1,
  updatedAt: 2,
  messages: [],
}

beforeEach(() => {
  vi.stubGlobal("Notification", FakeNotification)
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) =>
      key === NOTIFICATIONS_PREF_KEY ? "true" : null
    ),
  })
})

afterEach(() => {
  FakeNotification.instances = []
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("showRunNotification", () => {
  it("opens the completed thread when the notification is clicked", () => {
    const focus = vi.spyOn(window, "focus").mockImplementation(() => {})
    const openThread = vi.fn()

    showRunNotification({ ...thread, status: "running" }, thread, openThread)

    expect(FakeNotification.instances).toHaveLength(1)
    const notification = FakeNotification.instances[0]
    if (!notification) throw new Error("Notification was not created")
    notification.onclick?.()

    expect(focus).toHaveBeenCalledOnce()
    expect(openThread).toHaveBeenCalledOnce()
    expect(openThread).toHaveBeenCalledWith("thread-123")
    expect(notification.close).toHaveBeenCalledOnce()
  })
})

describe("runNotificationContent", () => {
  const running: AgentThread = { ...thread, status: "running" }

  it("says what the run answered and which PR it opened", () => {
    const content = runNotificationContent(running, {
      ...thread,
      pr: {
        number: 42,
        title: "Better notifications",
        state: "draft",
        headRef: "open-swe/notify",
        baseRef: "main",
        url: "https://github.com/langchain-ai/open-swe/pull/42",
      },
      diffStats: { files: 2, additions: 30, deletions: 4 },
      lastReply: {
        text: "Should I also **email** you? See [the plan](https://x.dev).",
        awaitingChoice: true,
      },
    })

    expect(content).toEqual({
      body: "Needs your decision · Opened PR #42 (+30 −4)\nShould I also email you? See the plan.",
      requireInteraction: true,
    })
  })

  it("stays quiet when the run decided nobody needed a reply", () => {
    expect(
      runNotificationContent(running, {
        ...thread,
        lastReply: { text: null, awaitingChoice: false },
      })
    ).toBeNull()
  })
})

describe("useRunCompletionNotifier", () => {
  it.each(["finished", "error", "interrupted"] as const)(
    "keeps Slack threads quiet on %s while dashboard threads still notify",
    (status) => {
      const slack: AgentThread = {
        ...thread,
        id: "slack-thread",
        source: "slack",
        status: "running",
      }
      const dashboard: AgentThread = {
        ...thread,
        source: "dashboard",
        status: "running",
      }
      const openThread = vi.fn()
      const { rerender } = renderHook(
        (threads: Array<AgentThread>) =>
          useRunCompletionNotifier(threads, undefined, openThread),
        { initialProps: [slack, dashboard] }
      )

      rerender([
        { ...slack, status },
        { ...dashboard, status },
      ])

      expect(FakeNotification.instances).toHaveLength(1)
      expect(FakeNotification.instances[0]?.options?.tag).toBe(
        `run-${dashboard.id}`
      )
    }
  )

  it("notifies for the coordinator but keeps independently listed workers quiet", () => {
    const coordinator: AgentThread = {
      ...thread,
      status: "running",
      taskMembership: { role: "coordinator", taskId: "task-123" },
    }
    const worker: AgentThread = {
      ...thread,
      id: "worker-123",
      status: "running",
      taskMembership: {
        role: "worker",
        taskId: "task-123",
        coordinatorThreadId: coordinator.id,
      },
    }
    const openThread = vi.fn()
    const { rerender } = renderHook(
      (threads: Array<AgentThread>) =>
        useRunCompletionNotifier(threads, undefined, openThread),
      { initialProps: [coordinator, worker] }
    )

    rerender([
      { ...coordinator, status: "finished" },
      { ...worker, status: "finished" },
    ])

    expect(FakeNotification.instances).toHaveLength(1)
    expect(FakeNotification.instances[0]?.options?.tag).toBe(
      `run-${coordinator.id}`
    )
  })
})
