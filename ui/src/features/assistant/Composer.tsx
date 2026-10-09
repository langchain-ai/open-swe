import { useCallback, useEffect } from "react"
import {
  AttachmentPrimitive,
  ComposerPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Select } from "@langchain/macaw-components/Select"
import { ArrowUpIcon } from "@phosphor-icons/react/dist/ssr/ArrowUp"
import { PlusIcon } from "@phosphor-icons/react/dist/ssr/Plus"
import { StopIcon } from "@phosphor-icons/react/dist/ssr/Stop"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
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
    <AttachmentPrimitive.Root className="flex items-center gap-2 rounded-xl border border-default p-2 text-xs">
      {(file || image?.image) && (
        <img
          ref={previewRef}
          src={image?.image}
          alt={attachment.name}
          className="size-12 rounded-sm object-cover"
        />
      )}
      <span className="text-primary">{attachment.name}</span>
      <AttachmentPrimitive.Remove asChild>
        <IconButton
          icon={XIcon}
          label={`Remove ${attachment.name}`}
          color="secondary"
          variant="plain"
          size="xs"
        />
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
      <ComposerPrimitive.AttachmentDropzone className="rounded-3xl border border-default bg-surface-level-1 p-3 shadow-xs data-[dragging=true]:border-brand">
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
          className="max-h-48 min-h-14 w-full resize-none bg-transparent px-2 py-2 text-sm leading-6 text-primary outline-none placeholder:text-placeholder"
        />
        <div className="flex flex-wrap items-center gap-2">
          <ComposerPrimitive.AddAttachment asChild>
            <IconButton
              icon={PlusIcon}
              label="Attach images"
              color="secondary"
              variant="plain"
              size="md"
              round
            />
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
            triggerClassName="max-w-48 rounded-full px-2 py-1.5 text-xs text-secondary hover:bg-surface-level-2"
          />
          {!thread && (
            <>
              <Select
                aria-label="Repository"
                hideSearch={false}
                searchPlaceholder="Search repositories…"
                value={typeof config?.repo === "string" ? config.repo : ""}
                onChange={(repo) =>
                  update({ repo: repo || null, repo_explicitly_none: !repo })
                }
                options={[
                  { value: "", label: "No repository" },
                  ...(repos.data?.repositories.map((repo) => ({
                    value: repo.full_name,
                  })) ?? []),
                ]}
                triggerClassName="max-w-48"
              />
              {workspaces.length > 0 && (
                <Select
                  aria-label="Workspace"
                  value={
                    typeof config?.environment === "string"
                      ? config.environment
                      : (workspaceQuery.data?.default_slug ?? "")
                  }
                  onChange={(environment) => {
                    if (environment) update({ environment })
                  }}
                  options={workspaces.map((workspace) => ({
                    value: workspace.slug,
                  }))}
                  triggerClassName="max-w-32"
                />
              )}
            </>
          )}
          <div className="ml-auto">
            {running ? (
              <ComposerPrimitive.Cancel asChild>
                <IconButton
                  icon={StopIcon}
                  iconWeight="fill"
                  label="Stop run"
                  size="md"
                  round
                />
              </ComposerPrimitive.Cancel>
            ) : (
              <ComposerPrimitive.Send asChild>
                <IconButton
                  icon={ArrowUpIcon}
                  label="Send message"
                  size="md"
                  round
                />
              </ComposerPrimitive.Send>
            )}
          </div>
        </div>
      </ComposerPrimitive.AttachmentDropzone>
    </ComposerPrimitive.Root>
  )
}
