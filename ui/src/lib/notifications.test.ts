/** @vitest-environment jsdom */

import { renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { AgentThread } from "@/features/agents/lib/types"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"
import {
  NOTIFICATIONS_PREF_KEY,
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

    showRunNotification(thread, openThread)

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

describe("useRunCompletionNotifier", () => {
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
