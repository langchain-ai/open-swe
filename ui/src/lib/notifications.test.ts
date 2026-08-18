/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import type { AgentThread } from "@/features/agents/lib/types"
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

afterEach(() => {
  FakeNotification.instances = []
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("showRunNotification", () => {
  it("opens the completed thread when the notification is clicked", () => {
    vi.stubGlobal("Notification", FakeNotification)
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) =>
        key === NOTIFICATIONS_PREF_KEY ? "true" : null
      ),
    })
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
