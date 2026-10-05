import { useEffect, useRef, useState } from "react"
import { CopilotChat, useAgent, useCopilotKit } from "@copilotkit/react-core/v2"
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { AgentGitPanel } from "@/features/agents/components/AgentGitPanel"
import { InlinePlanArtifact } from "@/features/agents/components/InlinePlanArtifact"
import { ModelPicker } from "@/features/agents/components/ModelPicker"
import { ThreadPullRequests } from "@/features/agents/components/ThreadPullRequests"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { agentsApi } from "@/features/agents/lib/api"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import {
  useAgentThreadPullRequestStatus,
  useWorkspaceOptions,
} from "@/features/agents/lib/queries"
import { modelConfigurable } from "@/features/agents/lib/stream/promptMessage"
import { reportError } from "@/lib/errorReporting"
import { pageTitle } from "@/lib/pageTitle"
import { useProfile, useRepos } from "@/lib/profile"
import { useRunSettings } from "./CopilotProvider"
import type { RunConfigurable } from "./CopilotProvider"
import { visibleMessages } from "./visibleMessages"

const IMAGE_TYPES = "image/png,image/jpeg,image/gif,image/webp"

function AssistantMarkdown({ content }: { content: string }) {
  return <Markdown content={content} />
}

const Hidden = () => null

function RunSettingsBar({
  isNew,
  configurable,
  onChange,
}: {
  isNew: boolean
  configurable: RunConfigurable
  onChange: (next: RunConfigurable) => void
}) {
  const { models } = useModelOptions()
  const repos = useRepos()
  const workspaceQuery = useWorkspaceOptions(isNew)
  const workspaces = workspaceQuery.data?.workspaces ?? []
  const selection =
    configurable.model_selection !== "auto" &&
    configurable.agent_model_id &&
    configurable.agent_effort
      ? {
          modelId: configurable.agent_model_id,
          effort: configurable.agent_effort,
        }
      : null
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ModelPicker
        models={models}
        selection={selection}
        onSelectionChange={(value) =>
          onChange({
            ...configurable,
            agent_model_id: undefined,
            agent_effort: undefined,
            ...modelConfigurable(value, true),
          })
        }
        triggerClassName="max-w-48 rounded-full px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted"
      />
      {isNew && (
        <select
          aria-label="Repository"
          value={configurable.repo ?? ""}
          onChange={(event) =>
            onChange({
              ...configurable,
              repo: event.target.value || null,
              repo_explicitly_none: !event.target.value,
            })
          }
          className="max-w-40 bg-transparent text-xs"
        >
          <option value="">No repository</option>
          {repos.data?.repositories.map((repo) => (
            <option key={repo.full_name} value={repo.full_name}>
              {repo.full_name}
            </option>
          ))}
        </select>
      )}
      {isNew && workspaces.length > 0 && (
        <select
          aria-label="Workspace"
          value={
            configurable.environment ?? workspaceQuery.data?.default_slug ?? ""
          }
          onChange={(event) =>
            onChange({ ...configurable, environment: event.target.value })
          }
          className="max-w-32 bg-transparent text-xs"
        >
          {workspaces.map((workspace) => (
            <option key={workspace.slug} value={workspace.slug}>
              {workspace.slug}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

/** One conversation. Stays mounted when a new thread gets its URL, so the first run keeps streaming. */
export function Conversation({
  threadId,
  isNew,
  initialRepo,
}: {
  threadId: string
  isNew: boolean
  initialRepo?: string | null
}) {
  const navigate = useNavigate()
  const profile = useProfile()
  const { defaultSelection } = useModelOptions()
  const { configurable, setConfigurable } = useRunSettings()
  const { copilotkit } = useCopilotKit()
  const { agent } = useAgent({ agentId: "default" })
  const [running, setRunning] = useState(false)
  const [panelCollapsed, setPanelCollapsed] = useState(true)
  const thread = useQuery({
    queryKey: ["copilot-threads", threadId],
    queryFn: () => agentsApi.getThread(threadId),
    enabled: !isNew,
  })
  const metadata = thread.data
  const refetchThread = thread.refetch
  const checks = useAgentThreadPullRequestStatus(
    threadId,
    Boolean(metadata?.pullRequests?.length)
  )
  const seededFor = useRef<string | null>(null)

  useEffect(() => {
    if (seededFor.current === threadId || !profile.data) return
    if (!isNew && !metadata) return
    seededFor.current = threadId
    setConfigurable({
      ...modelConfigurable(
        metadata?.modelSelection === "auto"
          ? null
          : metadata?.model && metadata.effort
            ? { modelId: metadata.model, effort: metadata.effort }
            : defaultSelection
      ),
      ...(isNew
        ? initialRepo === null
          ? { repo_explicitly_none: true }
          : { repo: initialRepo ?? profile.data.default_repo ?? null }
        : {}),
    })
  }, [
    defaultSelection,
    initialRepo,
    isNew,
    metadata,
    profile.data,
    setConfigurable,
    threadId,
  ])

  useEffect(() => {
    const subscription = agent.subscribe({
      onRunStartedEvent: () => {
        setRunning(true)
        void refetchThread()
        setConfigurable((current) =>
          current?.model_selection_changed
            ? { ...current, model_selection_changed: false }
            : current
        )
        if (isNew)
          void navigate({
            to: "/copilot/$threadId",
            params: { threadId },
            replace: true,
          })
      },
      onRunFinalized: () => {
        setRunning(false)
        void refetchThread()
      },
    })
    return () => subscription.unsubscribe()
  }, [agent, isNew, navigate, refetchThread, setConfigurable, threadId])

  const title = metadata?.title
  useEffect(() => {
    if (title) document.title = pageTitle(title)
  }, [title])

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <div
        data-testid="copilotkit-conversation"
        className="flex min-h-0 min-w-0 flex-1 flex-col"
      >
        <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium">
            {title ?? "New conversation"}
          </h1>
          {configurable && (
            <RunSettingsBar
              isNew={isNew}
              configurable={configurable}
              onChange={setConfigurable}
            />
          )}
          {metadata && (
            <button
              className="text-xs"
              onClick={() => setPanelCollapsed(!panelCollapsed)}
            >
              Files and terminal
            </button>
          )}
        </header>
        <CopilotChat
          agentId="default"
          threadId={threadId}
          className="min-h-0 flex-1"
          attachments={{ enabled: true, accept: IMAGE_TYPES }}
          onStop={() => {
            agentsApi
              .cancelThread(threadId)
              .catch((error: unknown) =>
                reportError({ title: "Couldn't stop the run", error })
              )
          }}
          messageView={{
            transformMessages: visibleMessages,
            assistantMessage: {
              markdownRenderer: AssistantMarkdown,
              thumbsUpButton: Hidden,
              thumbsDownButton: Hidden,
              readAloudButton: Hidden,
              regenerateButton: Hidden,
            },
          }}
        />
        {metadata && (
          <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pb-4">
            <WorkflowApprovalCard
              threadId={metadata.id}
              pollWhileActive={running}
            />
            {(metadata.planStatus === "ready" ||
              metadata.planStatus === "shared") && (
              <InlinePlanArtifact threadId={metadata.id} />
            )}
            <ThreadPullRequests
              pullRequests={metadata.pullRequests ?? []}
              health={checks.data?.pullRequests}
              healthUnavailable={checks.isError}
              onFix={async (pr, scope) => {
                const { prompt } = await agentsApi.getThreadPullRequestContext(
                  metadata.id,
                  pr.repoFullName,
                  pr.number,
                  scope
                )
                agent.addMessage({
                  id: crypto.randomUUID(),
                  role: "user",
                  content: prompt,
                })
                await copilotkit.runAgent({ agent })
              }}
              fixDisabled={running}
            />
          </div>
        )}
      </div>
      {metadata && (
        <AgentGitPanel
          thread={metadata}
          collapsed={panelCollapsed}
          onCollapsedChange={setPanelCollapsed}
        />
      )}
    </div>
  )
}
