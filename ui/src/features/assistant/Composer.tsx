import { useCallback, useEffect } from "react"
import {
  AttachmentPrimitive,
  ComposerPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react"
import { ArrowUp, Plus, Square, X } from "lucide-react"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import { ModelPicker } from "@/features/agents/components/ModelPicker"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import { modelConfigurable } from "@/features/agents/lib/stream/promptMessage"
import { useWorkspaceOptions } from "@/features/agents/lib/queries"
import { useProfile, useRepos } from "@/lib/profile"
import { useThreadMetadata } from "./AssistantProvider"

function Attachment() {
  const attachment = useAuiState((state) => state.attachment)
  const file = attachment.file
  const previewRef = useCallback(
    (element: HTMLImageElement | null) => {
      if (!element || !file) return
      const url = URL.createObjectURL(file)
      element.src = url
      return () => URL.revokeObjectURL(url)
    },
    [file]
  )
  const image = attachment.content?.find((part) => part.type === "image")
  return (
    <AttachmentPrimitive.Root className="flex items-center gap-2 rounded-xl border border-border p-2 text-xs">
      {(file || image?.image) && (
        <img
          ref={previewRef}
          src={image?.image}
          alt={attachment.name}
          className="size-12 rounded object-cover"
        />
      )}
      <span>{attachment.name}</span>
      <AttachmentPrimitive.Remove aria-label={`Remove ${attachment.name}`}>
        <X className="size-4" />
      </AttachmentPrimitive.Remove>
    </AttachmentPrimitive.Root>
  )
}

export function Composer({ initialRepo }: { initialRepo?: string | null }) {
  const aui = useAui()
  const running = useAuiState((state) => state.thread.isRunning)
  const disabled = useAuiState((state) => state.thread.isDisabled)
  const hasAttachments = useAuiState(
    (state) => state.composer.attachments.length > 0
  )
  const config = useAuiState((state) => state.composer.runConfig.custom)
  const { data: thread } = useThreadMetadata()
  const { models, defaultSelection } = useModelOptions()
  const profile = useProfile()
  const repos = useRepos()
  const workspaceQuery = useWorkspaceOptions(!thread)
  const workspaces = workspaceQuery.data?.workspaces ?? []
  const update = (values: Record<string, unknown>) =>
    aui.composer().setRunConfig({ custom: { ...config, ...values } })

  useEffect(() => {
    if (config || !profile.data || disabled) return
    aui.composer().setRunConfig({
      custom: {
        ...modelConfigurable(
          thread?.modelSelection === "auto"
            ? null
            : thread?.model && thread.effort
              ? { modelId: thread.model, effort: thread.effort }
              : defaultSelection
        ),
        ...(initialRepo === null
          ? { repo_explicitly_none: true }
          : (initialRepo ?? profile.data.default_repo)
            ? { repo: initialRepo ?? profile.data.default_repo }
            : {}),
      },
    })
  }, [
    aui,
    config,
    defaultSelection,
    disabled,
    initialRepo,
    profile.data,
    thread,
  ])

  const selection =
    config?.model_selection !== "auto" &&
    typeof config?.agent_model_id === "string" &&
    typeof config.agent_effort === "string"
      ? { modelId: config.agent_model_id, effort: config.agent_effort }
      : null

  return (
    <ComposerPrimitive.Root className="w-full">
      <ComposerPrimitive.AttachmentDropzone className="rounded-3xl border border-border bg-card p-3 shadow-xs focus-within:border-muted-foreground/40 data-[dragging=true]:border-primary">
        <div className="flex flex-wrap gap-2 empty:hidden">
          <ComposerPrimitive.Attachments>
            {() => <Attachment />}
          </ComposerPrimitive.Attachments>
        </div>
        <ComposerPrimitive.Input
          aria-label="Message input"
          placeholder={
            disabled
              ? "Sending is unavailable in this thread"
              : running
                ? "Draft your next message…"
                : "Send a message…"
          }
          rows={2}
          className="max-h-48 min-h-14 w-full resize-none bg-transparent px-2 py-2 text-sm leading-6 outline-none"
        />
        <div className="flex flex-wrap items-center gap-2">
          <ComposerPrimitive.AddAttachment
            aria-label="Attach images"
            className="rounded-full p-2 hover:bg-muted"
          >
            <Plus className="size-4" />
          </ComposerPrimitive.AddAttachment>
          <ModelPicker
            models={models}
            selection={selection}
            onSelectionChange={(value) =>
              update({
                agent_model_id: undefined,
                agent_effort: undefined,
                ...modelConfigurable(value),
              })
            }
            disabled={disabled}
            requireImageSupport={hasAttachments}
            triggerClassName="max-w-48 rounded-full px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted"
          />
          {!thread && (
            <>
              <RepoSelector
                label="Repository"
                repos={repos.data?.repositories}
                selectedRepo={
                  typeof config?.repo === "string" ? config.repo : null
                }
                autoSelect={!config?.repo_explicitly_none}
                disabled={disabled || !config}
                onRepoChange={(repo) =>
                  update({ repo, repo_explicitly_none: !repo })
                }
                triggerClassName="max-w-40 text-xs"
              />
              {workspaces.length > 0 && (
                <select
                  aria-label="Workspace"
                  value={
                    typeof config?.environment === "string"
                      ? config.environment
                      : (workspaceQuery.data?.default_slug ?? "")
                  }
                  onChange={(event) =>
                    update({ environment: event.target.value })
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
            </>
          )}
          <div className="ml-auto">
            {running ? (
              <ComposerPrimitive.Cancel
                aria-label="Stop run"
                className="rounded-full bg-primary p-2.5 text-primary-foreground disabled:opacity-40"
              >
                <Square className="size-3.5 fill-current" />
              </ComposerPrimitive.Cancel>
            ) : (
              <ComposerPrimitive.Send
                aria-label="Send message"
                className="rounded-full bg-primary p-2.5 text-primary-foreground disabled:opacity-30"
              >
                <ArrowUp className="size-4" />
              </ComposerPrimitive.Send>
            )}
          </div>
        </div>
      </ComposerPrimitive.AttachmentDropzone>
    </ComposerPrimitive.Root>
  )
}
