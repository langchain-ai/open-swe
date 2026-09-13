import { useState } from "react"
import {
  ComposerPrimitive,
  QueueItemPrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react"
import { useLangChainError } from "@assistant-ui/react-langchain"
import { ArrowDown } from "lucide-react"
import { AgentGitPanel } from "@/features/agents/components/AgentGitPanel"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import { InlinePlanArtifact } from "@/features/agents/components/InlinePlanArtifact"
import { ThreadPullRequests } from "@/features/agents/components/ThreadPullRequests"
import { ThreadFeedbackCard } from "@/features/agents/components/ThreadFeedbackCard"
import { useAgentThreadPullRequestStatus } from "@/features/agents/lib/queries"
import { agentsApi } from "@/features/agents/lib/api"
import { useSession } from "@/lib/session"
import { AssistantMessage } from "./Message"
import { Composer } from "./Composer"
import { useProductState } from "./AssistantProvider"

export function Conversation({ initialRepo }: { initialRepo?: string }) {
  const aui = useAui()
  const { thread, error: requestError, queueErrors } = useProductState()
  const running = useAuiState((state) => state.thread.isRunning)
  const loading = useAuiState((state) => state.thread.isLoading)
  const empty = useAuiState((state) => state.thread.messages.length === 0)
  const queued = useAuiState((state) => state.composer.queue.length > 0)
  const streamError = useLangChainError()
  const error =
    requestError ??
    (streamError instanceof Error
      ? streamError.message
      : streamError
        ? String(streamError)
        : undefined)
  const [panelCollapsed, setPanelCollapsed] = useState(true)
  const session = useSession()
  const checks = useAgentThreadPullRequestStatus(
    thread?.id ?? "",
    Boolean(thread?.pullRequests?.length)
  )

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <ThreadPrimitive.Root
        data-testid="assistant-ui-conversation"
        className="flex min-h-0 min-w-0 flex-1 flex-col"
      >
        <header className="flex items-center gap-3 border-b border-border px-5 py-3">
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium">
            {thread?.title ?? "New conversation"}
          </h1>
          {thread && (
            <button
              className="text-xs"
              onClick={() => setPanelCollapsed(!panelCollapsed)}
            >
              Files and terminal
            </button>
          )}
        </header>
        <ThreadPrimitive.Viewport
          turnAnchor="top"
          aria-label="Conversation messages"
          className="min-h-0 flex-1 [scrollbar-gutter:stable_both-edges] overflow-x-hidden overflow-y-auto"
        >
          <div className="mx-auto w-full max-w-3xl px-5 py-6 sm:px-8">
            {loading && empty ? (
              <p
                role="status"
                className="py-16 text-center text-sm text-muted-foreground"
              >
                Loading conversation…
              </p>
            ) : (
              empty &&
              !running && (
                <h2 className="py-16 text-center text-2xl">
                  What are we working on?
                </h2>
              )
            )}
            <ThreadPrimitive.Messages>
              {() => <AssistantMessage />}
            </ThreadPrimitive.Messages>
            {thread && (
              <WorkflowApprovalCard
                threadId={thread.id}
                pollWhileActive={running}
              />
            )}
            {thread &&
              (thread.planStatus === "ready" ||
                thread.planStatus === "shared") && (
                <InlinePlanArtifact threadId={thread.id} />
              )}
            <ComposerPrimitive.Queue>
              {({ queueItem }) => (
                <div className="my-3 ml-auto max-w-[85%] rounded-2xl border border-dashed border-border p-3 text-sm">
                  <p className="mb-1 text-xs text-muted-foreground">
                    {queueErrors[queueItem.id]
                      ? "Could not send"
                      : "Queued next"}
                  </p>
                  <QueueItemPrimitive.Text />
                  {queueErrors[queueItem.id] && (
                    <>
                      <p role="alert" className="text-destructive">
                        {queueErrors[queueItem.id]}
                      </p>
                      <QueueItemPrimitive.Steer className="mr-3 underline">
                        Retry message
                      </QueueItemPrimitive.Steer>
                      <QueueItemPrimitive.Remove className="underline">
                        Discard
                      </QueueItemPrimitive.Remove>
                    </>
                  )}
                </div>
              )}
            </ComposerPrimitive.Queue>
          </div>
        </ThreadPrimitive.Viewport>
        <div className="relative mx-auto w-full max-w-3xl px-4 pt-2 pb-4">
          <ThreadPrimitive.ScrollToBottom
            aria-label="Scroll to bottom"
            className="absolute -top-10 left-1/2 rounded-full border border-border bg-background p-2 shadow-sm disabled:invisible"
          >
            <ArrowDown className="size-4" />
          </ThreadPrimitive.ScrollToBottom>
          {error && (
            <p role="alert" className="mb-3 text-sm text-destructive">
              {error}
            </p>
          )}
          {thread && (
            <>
              {!running && !queued && (
                <ThreadFeedbackCard
                  threadId={thread.id}
                  login={session.data?.login ?? null}
                />
              )}
              {thread.codeChannelUrl && (
                <a
                  href={thread.codeChannelUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mb-2 block text-xs underline"
                >
                  Open code channel
                </a>
              )}
              <ThreadPullRequests
                pullRequests={thread.pullRequests ?? []}
                health={checks.data?.pullRequests}
                healthUnavailable={checks.isError}
                onFix={async (pr) => {
                  const result = await agentsApi.getThreadPullRequestContext(
                    thread.id,
                    pr.repoFullName,
                    pr.number
                  )
                  aui.composer().setText(result.prompt)
                }}
                fixDisabled={running}
              />
            </>
          )}
          <Composer initialRepo={initialRepo} />
        </div>
      </ThreadPrimitive.Root>
      {thread && (
        <AgentGitPanel
          thread={thread}
          collapsed={panelCollapsed}
          onCollapsedChange={setPanelCollapsed}
        />
      )}
    </div>
  )
}
