import { useCallback, useEffect } from "react"
import {
  AttachmentPrimitive,
  ComposerPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ArrowUp, Plus, Square, X } from "@/components/glyphs"
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
    <AttachmentPrimitive.Root className="flex items-center gap-2 rounded-control border border-line bg-panel p-2 text-label">
      {(file || image?.image) && (
        <img
          ref={previewRef}
          src={image?.image}
          alt={attachment.name}
          className="size-12 rounded-compact object-cover"
        />
      )}
      <span>{attachment.name}</span>
      <AttachmentPrimitive.Remove
        aria-label={`Remove ${attachment.name}`}
        className="inline-flex size-control-sm items-center justify-center rounded-compact text-ink-subtle hover:bg-hover hover:text-ink"
      >
        <Icon icon={X} size="sm" />
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
      <ComposerPrimitive.AttachmentDropzone className="rounded-shell border border-line bg-panel p-3 transition-colors duration-fast ease-out-quint focus-within:border-line-strong data-[dragging=true]:border-primary motion-reduce:transition-none">
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
          className="max-h-48 min-h-14 w-full resize-none bg-transparent px-2 py-2 text-body text-ink outline-none placeholder:text-ink-subtle"
        />
        <div className="flex flex-wrap items-center gap-2">
          <ComposerPrimitive.AddAttachment
            aria-label="Attach images"
            className="inline-flex size-control items-center justify-center rounded-control text-ink-subtle hover:bg-hover hover:text-ink"
          >
            <Icon icon={Plus} />
          </ComposerPrimitive.AddAttachment>
          <ModelPicker
            models={models}
            selection={selection}
            onSelectionChange={(value) =>
              update({
                agent_model_id: undefined,
                agent_effort: undefined,
                model_selection_changed: false,
                model_selection_action_id: crypto.randomUUID(),
                ...modelConfigurable(value, true),
              })
            }
            disabled={disabled}
            requireImageSupport={hasAttachments}
            triggerClassName="h-control-sm max-w-48 rounded-compact px-2 text-label text-ink-subtle hover:bg-hover"
          />
          {!thread && (
            <>
              <select
                aria-label="Repository"
                title={
                  typeof config?.repo === "string" ? config.repo : undefined
                }
                value={typeof config?.repo === "string" ? config.repo : ""}
                onChange={(event) =>
                  update({
                    repo: event.target.value || null,
                    repo_explicitly_none: !event.target.value,
                  })
                }
                className="h-control-sm max-w-40 rounded-compact bg-transparent px-1 text-label text-ink-subtle hover:bg-hover"
              >
                <option value="">No repository</option>
                {repos.data?.repositories.map((repo) => (
                  <option key={repo.full_name} value={repo.full_name}>
                    {repo.full_name}
                  </option>
                ))}
              </select>
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
                  className="h-control-sm max-w-32 rounded-compact bg-transparent px-1 text-label text-ink-subtle hover:bg-hover"
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
                className="inline-flex size-control items-center justify-center rounded-control bg-primary text-primary-ink shadow-control disabled:opacity-50"
              >
                <Icon icon={Square} size="sm" />
              </ComposerPrimitive.Cancel>
            ) : (
              <ComposerPrimitive.Send
                aria-label="Send message"
                className="inline-flex size-control items-center justify-center rounded-control bg-primary text-primary-ink shadow-control disabled:opacity-50"
              >
                <Icon icon={ArrowUp} />
              </ComposerPrimitive.Send>
            )}
          </div>
        </div>
      </ComposerPrimitive.AttachmentDropzone>
    </ComposerPrimitive.Root>
  )
}
