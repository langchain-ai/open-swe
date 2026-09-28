/** @vitest-environment jsdom */

import type { ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AgentThreadView } from "./AgentThreadView"
import type { AgentPromptBarProps } from "./AgentPromptBar"
import type { MessagesProps } from "./messages"
import type { QueuedTurn } from "@/features/agents/lib/transcript/reducer"
import type { AgentThread } from "@/features/agents/lib/types"
import type { SubmitAgentMessageVariables } from "@/features/agents/lib/provider/useSubmitAgentMessage"

let composer: AgentPromptBarProps
let messages: MessagesProps
const cancelRun = vi.fn<(threadId: string, runId: string) => Promise<void>>()
const sendMessage = {
  mutateAsync:
    vi.fn<(variables: SubmitAgentMessageVariables) => Promise<void>>(),
  mutate: vi.fn<(variables: SubmitAgentMessageVariables) => void>(),
  isPending: false,
}
const source = {
  kind: "transcript" as "transcript" | "stream",
  messages: [],
  queued: [] as Array<QueuedTurn>,
  cancelQueued: vi.fn<(id: string) => Promise<boolean>>(),
  stop: vi.fn<() => Promise<boolean>>(),
  isRunning: false,
  isHydrating: false,
  hydration: Promise.resolve(),
  connection: { status: "connected" },
}
const selection = { modelId: "openai:gpt-6-sol", effort: "high" }

vi.mock("@/features/agents/components/AgentPromptBar", () => ({
  AgentPromptBar: (props: AgentPromptBarProps) => {
    composer = props
    return null
  },
}))
vi.mock("@/features/agents/components/AgentGitPanel", () => ({
  AgentGitPanel: () => null,
}))
vi.mock("@/features/agents/components/AgentThreadHeader", () => ({
  AgentThreadHeader: () => null,
}))
vi.mock("@/features/agents/components/ThreadPullRequests", () => ({
  ThreadPullRequests: () => null,
}))
vi.mock("@/features/agents/components/ThreadFeedbackCard", () => ({
  ThreadFeedbackCard: () => null,
}))
vi.mock("@/features/agents/components/messages", () => ({
  Messages: (props: MessagesProps) => {
    messages = props
    return null
  },
}))
vi.mock("@/features/agents/lib/api", () => ({
  agentsApi: {
    cancelRun: (...args: Parameters<typeof cancelRun>) => cancelRun(...args),
  },
}))
vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))
vi.mock("@/features/agents/components/PullRequestPreview", () => ({
  PullRequestPreviewProvider: ({ children }: { children: ReactNode }) =>
    children,
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useRenameAgentThread: () => ({}),
  useAgentSkills: () => ({ data: [] }),
  useAgentThreadPullRequestStatus: () => ({}),
  agentThreadKeys: { detail: (id: string) => ["thread", id] },
}))
vi.mock("@/features/agents/lib/provider/useSubmitAgentMessage", () => ({
  useSubmitAgentMessage: () => sendMessage,
}))
vi.mock("@/features/agents/lib/provider/useModelOptions", () => ({
  useModelOptions: () => ({
    models: [{ id: selection.modelId, efforts: [selection.effort] }],
    defaultSelection: selection,
  }),
}))
vi.mock("@/features/agents/lib/threadSource/ThreadSourceProvider", () => ({
  useThreadSource: () => source,
}))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "alice", follow_up_behavior: "queue" } }),
}))
vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => true }))

function setup(modelSelection: AgentThread["modelSelection"] = "explicit") {
  const thread: AgentThread = {
    id: "thread-1",
    title: "Fix routing",
    repo: "open-swe",
    repoFullName: "langchain-ai/open-swe",
    branch: "main",
    model: selection.modelId,
    effort: selection.effort,
    modelSelection,
    status: "idle",
    viewed: true,
    createdAt: 0,
    updatedAt: 0,
    messages: [],
  }
  const queryClient = new QueryClient()
  const view = () => (
    <QueryClientProvider client={queryClient}>
      <AgentThreadView thread={thread} />
    </QueryClientProvider>
  )
  const rendered = render(view())
  return { thread, rerender: () => rendered.rerender(view()) }
}

async function submit() {
  await act(async () => {
    await composer.onSubmit?.("continue", [])
  })
}

beforeEach(() => {
  sendMessage.mutateAsync.mockReset().mockResolvedValue(undefined)
  sendMessage.mutate.mockReset()
  cancelRun.mockReset().mockResolvedValue(undefined)
  source.kind = "transcript"
  source.queued = []
  source.cancelQueued.mockReset().mockResolvedValue(true)
  source.stop.mockReset().mockResolvedValue(true)
  source.isRunning = false
})
afterEach(cleanup)

describe("AgentThreadView model selection", () => {
  function queuedSubmission(): QueuedTurn {
    const submission = sendMessage.mutateAsync.mock.lastCall![0]
    return {
      turnId: "queued-turn",
      runId: "queued-run",
      senderLogin: "alice",
      requestedAt: "2026-09-28T12:00:00Z",
      message: {
        id: submission.client_message_id ?? "queued-message",
        author: "user",
        timestamp: "2026-09-28T12:00:00Z",
        chunks: [{ kind: "text", text: submission.content }],
      },
    }
  }

  async function queueAuto() {
    source.isRunning = true
    const view = setup()
    act(() => composer.onSelectionChange?.(null))
    await submit()
    const queued = queuedSubmission()
    source.queued = [queued]
    view.rerender()
    return { ...view, queued }
  }

  it.each([
    ["transcript", "cancel"],
    ["transcript", "send now"],
    ["transcript", "stop"],
    ["stream", "cancel"],
    ["stream", "send now"],
    ["stream", "stop"],
  ] as const)("restores queued Auto after %s %s", async (kind, action) => {
    source.kind = kind
    const { queued } = await queueAuto()

    await act(async () => {
      if (action === "stop") await composer.onStop?.()
      else if (action === "send now")
        messages.onSteerQueuedMessage?.(queued.message.id)
      else messages.onRemoveQueuedMessage?.(queued.message.id)
    })
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: true })
    )
    expect(composer.selection).toBeNull()
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: false })
    )
  })

  it.each(["transcript", "stream", "stop"] as const)(
    "keeps Auto consumed when %s cancellation fails",
    async (kind) => {
      source.kind = kind === "stream" ? "stream" : "transcript"
      const { queued } = await queueAuto()
      cancelRun.mockRejectedValueOnce(new Error("Cancel failed"))
      source.cancelQueued.mockResolvedValueOnce(false)
      source.stop.mockResolvedValueOnce(false)
      await act(async () => {
        if (kind === "stop") await composer.onStop?.()
        else messages.onSteerQueuedMessage?.(queued.message.id)
      })
      expect(sendMessage.mutate).not.toHaveBeenCalled()
      await submit()
      expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
        expect.objectContaining({ model_selection_changed: false })
      )
    }
  )

  it("does not restore Auto when a different queued message is cancelled", async () => {
    const { rerender } = await queueAuto()
    await submit()
    const other = queuedSubmission()
    source.queued = [other]
    rerender()
    await act(async () => messages.onRemoveQueuedMessage?.(other.message.id))
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: false })
    )
  })

  it.each([false, true])(
    "preserves a newer picker choice when an old Auto run is cancelled (auto=%s)",
    async (auto) => {
      const { queued } = await queueAuto()
      act(() => composer.onSelectionChange?.(selection))
      if (auto) {
        act(() => composer.onSelectionChange?.(null))
        await submit()
      }
      await act(async () => messages.onRemoveQueuedMessage?.(queued.message.id))
      await submit()
      expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
        expect.objectContaining({
          model_id: auto ? null : selection.modelId,
          model_selection_changed: false,
        })
      )
    }
  )

  it("restores Auto when Stop withdraws a message before the queue acknowledges it", async () => {
    const { thread, rerender, queued } = await queueAuto()
    source.queued = []
    thread.pendingMessages = [
      {
        id: queued.message.id,
        content: "continue",
        createdAt: 0,
        status: "sending",
        queued: true,
      },
    ]
    rerender()
    await act(async () => composer.onStop?.())
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: true })
    )
  })

  it("ignores a withdrawn run's late error after Auto is resubmitted", async () => {
    const { queued } = await queueAuto()
    const withdrawn = sendMessage.mutateAsync.mock.lastCall![0]
    await act(async () => messages.onRemoveQueuedMessage?.(queued.message.id))
    await submit()
    withdrawn.onStartError?.()
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: false })
    )
  })

  it.each([false, true])(
    "consumes an Auto selection once and keeps Auto active (running=%s)",
    async (running) => {
      source.isRunning = running
      setup()
      act(() => composer.onSelectionChange?.(null))

      await submit()
      expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
        expect.objectContaining({
          content: "continue",
          images: [],
          model_id: null,
          effort: null,
          model_selection_changed: true,
          enqueue: running,
          onStartError: expect.any(Function),
        })
      )

      await submit()
      expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
        expect.objectContaining({
          model_id: null,
          effort: null,
          model_selection_changed: false,
        })
      )
      expect(composer.selection).toBeNull()

      act(() => composer.onSelectionChange?.(null))
      await submit()
      expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
        expect.objectContaining({ model_selection_changed: true })
      )
    }
  )

  it("does not treat inherited Auto as a new picker action", async () => {
    setup("auto")
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({
        model_id: null,
        model_selection_changed: false,
      })
    )
  })

  it("preserves the pending Auto selection when submission fails", async () => {
    setup()
    act(() => composer.onSelectionChange?.(null))
    sendMessage.mutateAsync.mockRejectedValueOnce(new Error("Send failed"))

    await expect(submit()).rejects.toThrow("Send failed")
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: true })
    )
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: false })
    )
  })

  it("keeps a pending Auto action for the message after an offload command", async () => {
    setup()
    act(() => composer.onSelectionChange?.(null))
    await act(async () => {
      await composer.onSubmit?.("/offload", [])
    })

    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: true })
    )
  })

  it("preserves Auto through steering until a queued run can apply it", async () => {
    source.isRunning = true
    setup()
    act(() => composer.onSelectionChange?.(null))
    await act(async () => {
      await composer.onSubmit?.("steer", [], { alternate: true })
    })
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({
        model_selection_changed: false,
        enqueue: false,
      })
    )
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: true, enqueue: true })
    )
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: false })
    )
  })

  it("restores Auto when the start fails after the optimistic send resolves", async () => {
    setup()
    act(() => composer.onSelectionChange?.(null))
    await submit()
    const submission = sendMessage.mutateAsync.mock.calls[0]![0]
    submission.onStartError?.()
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: true })
    )
  })

  it("does not restore an old Auto action over a newer consumed selection", async () => {
    setup()
    act(() => composer.onSelectionChange?.(null))
    await submit()
    const submission = sendMessage.mutateAsync.mock.calls[0]![0]
    act(() => composer.onSelectionChange?.(selection))
    act(() => composer.onSelectionChange?.(null))
    await submit()
    submission.onStartError?.()
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: false })
    )
  })

  it("preserves a newer picker action while a submission is pending", async () => {
    setup()
    act(() => composer.onSelectionChange?.(null))
    let resolveSubmission!: () => void
    const pending = new Promise<void>((resolve) => {
      resolveSubmission = resolve
    })
    sendMessage.mutateAsync.mockReturnValueOnce(pending)

    const submission = composer.onSubmit?.("continue", [])
    act(() => composer.onSelectionChange?.(selection))
    act(() => composer.onSelectionChange?.(null))
    await act(async () => {
      resolveSubmission()
      await submission
    })

    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: true })
    )
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_selection_changed: false })
    )
  })

  it("cancels a pending Auto action when an explicit model is picked", async () => {
    setup()
    act(() => composer.onSelectionChange?.(null))
    act(() => composer.onSelectionChange?.(selection))
    await submit()
    expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({
        model_id: selection.modelId,
        effort: selection.effort,
        model_selection_changed: false,
      })
    )
  })
})
