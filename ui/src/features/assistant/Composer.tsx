import { useCallback, useEffect } from "react"
import {
  AttachmentPrimitive,
  ComposerPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react"
import { ArrowUp, Map, Plus, Square, X } from "lucide-react"
import { ModelPicker } from "@/features/agents/components/ModelPicker"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import { modelConfigurable } from "@/features/agents/lib/stream/promptMessage"
import { useEnvironmentOptions } from "@/features/agents/lib/queries"
import { useProfile, useRepos } from "@/lib/profile"
import { useProductState } from "./AssistantProvider"

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

export function Composer({ initialRepo }: { initialRepo?: string }) {
  const aui = useAui()
  const running = useAuiState((state) => state.thread.isRunning)
  const empty = useAuiState((state) => state.composer.isEmpty)
  const disabled = useAuiState((state) => state.thread.isDisabled)
  const hasAttachments = useAuiState(
    (state) => state.composer.attachments.length > 0
  )
  const config = useAuiState((state) => state.composer.runConfig.custom)
  const { thread } = useProductState()
  const { models, defaultSelection } = useModelOptions()
  const profile = useProfile()
  const repos = useRepos()
  const environmentQuery = useEnvironmentOptions(!thread)
  const environments = environmentQuery.data?.environments ?? []
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
        ...((initialRepo ?? profile.data.default_repo)
          ? { repo: initialRepo ?? profile.data.default_repo }
          : {}),
        plan_mode: thread?.planMode ?? false,
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
                ? "Send a follow up…"
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
          <button
            type="button"
            disabled={disabled}
            aria-label="Plan mode"
            aria-pressed={config?.plan_mode === true}
            onClick={() => update({ plan_mode: !config?.plan_mode })}
            className={`flex items-center gap-1 rounded-full px-2 py-1.5 text-xs ${config?.plan_mode ? "bg-primary/10 text-primary" : "text-muted-foreground"}`}
          >
            <Map className="size-3.5" />
            Plan
          </button>
          {!thread && (
            <>
              <select
                aria-label="Repository"
                value={typeof config?.repo === "string" ? config.repo : ""}
                onChange={(event) =>
                  update({
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
              {environments.length > 0 && (
                <select
                  aria-label="Environment"
                  value={
                    typeof config?.environment === "string"
                      ? config.environment
                      : (environmentQuery.data?.default_slug ?? "")
                  }
                  onChange={(event) =>
                    update({ environment: event.target.value })
                  }
                  className="max-w-32 bg-transparent text-xs"
                >
                  {environments.map((environment) => (
                    <option key={environment.slug} value={environment.slug}>
                      {environment.slug}
                    </option>
                  ))}
                </select>
              )}
            </>
          )}
          <div className="ml-auto">
            {running && empty ? (
              <ComposerPrimitive.Cancel
                aria-label="Stop run"
                className="rounded-full bg-primary p-2.5 text-primary-foreground disabled:opacity-40"
              >
                <Square className="size-3.5 fill-current" />
              </ComposerPrimitive.Cancel>
            ) : (
              <ComposerPrimitive.Send
                aria-label={running ? "Send follow up" : "Send message"}
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
