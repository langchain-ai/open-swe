/** @vitest-environment jsdom */

import type { ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AgentThreadView } from "./AgentThreadView"
import type { AgentPromptBarProps } from "./AgentPromptBar"
import type { AgentThread } from "@/features/agents/lib/types"
import type { SubmitAgentMessageVariables } from "@/features/agents/lib/provider/useSubmitAgentMessage"

let composer: AgentPromptBarProps
const sendMessage = {
  mutateAsync:
    vi.fn<(variables: SubmitAgentMessageVariables) => Promise<void>>(),
  isPending: false,
}
const source = {
  kind: "transcript",
  messages: [],
  queued: [],
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
  Messages: () => null,
}))
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
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AgentThreadView thread={thread} />
    </QueryClientProvider>
  )
}

async function submit() {
  await act(async () => {
    await composer.onSubmit?.("continue", [])
  })
}

beforeEach(() => {
  sendMessage.mutateAsync.mockReset().mockResolvedValue(undefined)
  source.isRunning = false
})
afterEach(cleanup)

describe("AgentThreadView model selection", () => {
  it.each([false, true])(
    "consumes an Auto selection once and keeps Auto active (running=%s)",
    async (running) => {
      source.isRunning = running
      setup()
      act(() => composer.onSelectionChange?.(null))

      await submit()
      expect(sendMessage.mutateAsync).toHaveBeenLastCalledWith({
        content: "continue",
        images: [],
        model_id: null,
        effort: null,
        model_selection_changed: true,
        enqueue: running,
        onStartError: expect.any(Function),
      })

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
