import { ArrowDownIcon } from "@langchain/macaw-components/icons"
import { Text } from "@langchain/macaw-components/Text"
import { useEffect, useState } from "react"
import { ThreadPrimitive, useAui, useAuiState } from "@assistant-ui/react"
import { useLangChainError } from "@assistant-ui/react-langchain"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Link } from "@langchain/macaw-components/Link"
import { AgentGitPanel } from "@/features/agents/components/AgentGitPanel"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import { InlinePlanArtifact } from "@/features/agents/components/InlinePlanArtifact"
import { ThreadPullRequests } from "@/features/agents/components/ThreadPullRequests"
import { useAgentThreadPullRequestStatus } from "@/features/agents/lib/queries"
import { agentsApi } from "@/features/agents/lib/api"
import { pageTitle } from "@/lib/pageTitle"
import { AssistantMessage } from "./Message"
import { Composer } from "./Composer"
import { useThreadMetadata } from "./AssistantProvider"
import { ThreadLoadTiming } from "./ThreadLoadTiming"

export function Conversation({ initialRepo }: { initialRepo?: string | null }) {
  const aui = useAui()
  const threadId = useAuiState((state) => state.threadListItem.externalId)
  const { data: thread, error: requestError } = useThreadMetadata()
  const running = useAuiState((state) => state.thread.isRunning)
  const loading = useAuiState((state) => state.thread.isLoading)
  const empty = useAuiState((state) => state.thread.messages.length === 0)
  const streamError = useLangChainError()
  const error =
    requestError?.message ??
    (streamError instanceof Error
      ? streamError.message
      : streamError
        ? String(streamError)
        : undefined)
  const [panelCollapsed, setPanelCollapsed] = useState(true)
  const checks = useAgentThreadPullRequestStatus(
    thread?.id ?? "",
    Boolean(thread?.pullRequests?.length)
  )
  const title = thread?.title

  useEffect(() => {
    if (!title) return
    const documentTitle = pageTitle(title)
    document.title = documentTitle
    return () => {
      if (document.title === documentTitle)
        document.title = pageTitle("Assistant")
    }
  }, [title])

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      {threadId && <ThreadLoadTiming key={threadId} threadId={threadId} />}
      <ThreadPrimitive.Root
        data-testid="assistant-ui-conversation"
        className="flex min-h-0 min-w-0 flex-1 flex-col"
      >
        <header className="flex items-center gap-space-3 border-b border-default px-space-4 py-space-3">
          <Text
            as="h1"
            variant="h5"
            weight="medium"
            color="primary"
            className="min-w-0 flex-1 truncate"
          >
            {thread?.title ?? "New conversation"}
          </Text>
          {thread && (
            <Button
              color="secondary"
              variant="plain"
              aria-expanded={!panelCollapsed}
              onClick={() => setPanelCollapsed(!panelCollapsed)}
            >
              Files and terminal
            </Button>
          )}
        </header>
        <ThreadPrimitive.Viewport
          turnAnchor="top"
          aria-label="Conversation messages"
          className="min-h-0 flex-1 [scrollbar-gutter:stable_both-edges] overflow-x-hidden overflow-y-auto"
        >
          <div className="mx-auto w-full max-w-3xl px-space-4 py-space-5 sm:px-space-6">
            {loading && empty ? (
              <p
                role="status"
                className="py-space-9 text-center text-sm text-secondary"
              >
                Loading conversation…
              </p>
            ) : (
              empty &&
              !running && (
                <Text
                  as="h2"
                  variant="h1"
                  color="primary"
                  className="py-space-9 text-center"
                >
                  What are we working on?
                </Text>
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
          </div>
        </ThreadPrimitive.Viewport>
        <div className="relative mx-auto w-full max-w-3xl px-space-4 pt-space-2 pb-space-4">
          <ThreadPrimitive.ScrollToBottom asChild>
            <IconButton
              icon={ArrowDownIcon}
              label="Scroll to bottom"
              color="secondary"
              variant="outlined"
              size="md"
              round
              className="absolute -top-10 left-1/2 shadow-sm disabled:invisible"
            />
          </ThreadPrimitive.ScrollToBottom>
          {error && (
            <div role="alert" className="mb-space-3">
              <Banner intent="error">{error}</Banner>
            </div>
          )}
          {thread && (
            <>
              {thread.codeChannelUrl && (
                <Link
                  href={thread.codeChannelUrl}
                  target="_blank"
                  rel="noreferrer"
                  variant="xs"
                  className="mb-space-2 block"
                >
                  Open code channel
                </Link>
              )}
              <ThreadPullRequests
                pullRequests={thread.pullRequests ?? []}
                health={checks.data?.pullRequests}
                healthUnavailable={checks.isError}
                onFix={async (pr, scope) => {
                  const result = await agentsApi.getThreadPullRequestContext(
                    thread.id,
                    pr.repoFullName,
                    pr.number,
                    scope
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
