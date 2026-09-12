import { useEffect, useLayoutEffect, useMemo, useState } from "react"
import {
  AttachmentPrimitive,
  ComposerPrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react"
import { ArrowDown, ArrowUp, Map, Plus, Square, X } from "lucide-react"
import type { ReactNode } from "react"
import type { ChatComposerProps } from "../composer/ChatComposer"
import type { MessagesProps } from "../messages/types"
import type { ConversationRuntimeExtras } from "@/features/agents/lib/assistant-ui/conversationRuntime"
import { AssistantMessage } from "./AssistantMessage"
import { ModelPicker } from "../ModelPicker"
import { ContextWindowMeter } from "../composer/ContextWindowMeter"
import { InlinePlanArtifact } from "../InlinePlanArtifact"
import { serializeComposerFileLink } from "../composer/composerTrigger"

export interface AssistantConversationProps extends MessagesProps {
  composer: ChatComposerProps
  footer?: ReactNode
  isLoading?: boolean
  hydrationFailed?: boolean
}

function ComposerAttachment() {
  const attachment = useAuiState((s) => s.attachment)
  const [preview, setPreview] = useState<string>()
  useEffect(() => {
    if (!attachment.file) return
    const url = URL.createObjectURL(attachment.file)
    // The object URL belongs to this effect and is revoked when the file changes.
    // oxlint-disable-next-line react/set-state-in-effect
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [attachment.file])
  const image = attachment.content?.find((part) => part.type === "image")
  return (
    <AttachmentPrimitive.Root className="relative flex items-center gap-2 rounded-xl border border-border p-2 text-xs">
      {(preview || image?.image) && (
        <img
          src={preview ?? image?.image}
          alt={attachment.name}
          className="size-12 rounded-lg object-cover"
        />
      )}
      <span className="max-w-32 truncate">{attachment.name}</span>
      <AttachmentPrimitive.Remove
        aria-label={`Remove ${attachment.name}`}
        className="rounded p-1 hover:bg-muted"
      >
        <X className="size-3.5" />
      </AttachmentPrimitive.Remove>
    </AttachmentPrimitive.Root>
  )
}

function Composer({
  options,
  runtime,
}: {
  options: ChatComposerProps
  runtime: ReturnType<typeof useAui>
}) {
  const running = useAuiState((s) => s.thread.isRunning)
  const empty = useAuiState((s) => s.composer.isEmpty)
  const hasAttachments = useAuiState((s) => s.composer.attachments.length > 0)
  const disabled = useAuiState((s) => s.thread.isDisabled)
  const insert = (value: string) => {
    const composer = runtime.composer()
    const text = composer.getState().text
    composer.setText(
      `${text}${text && !text.endsWith(" ") ? " " : ""}${value} `
    )
  }
  return (
    <ComposerPrimitive.Root className="w-full">
      <ComposerPrimitive.AttachmentDropzone className="rounded-3xl border border-border bg-card p-3 shadow-xs transition-colors focus-within:border-muted-foreground/40 data-[dragging=true]:border-primary">
        <div className="mb-1 flex flex-wrap gap-2 empty:hidden">
          <ComposerPrimitive.Attachments>
            {() => <ComposerAttachment />}
          </ComposerPrimitive.Attachments>
        </div>
        <ComposerPrimitive.Input
          aria-label="Message input"
          placeholder={
            running
              ? "Send a follow up…"
              : (options.placeholder ?? "Send a message…")
          }
          autoFocus={options.autoFocus}
          rows={2}
          className="max-h-48 min-h-14 w-full resize-none bg-transparent px-2 py-1 text-sm leading-6 outline-none placeholder:text-muted-foreground/60"
          onKeyDown={(event) => {
            if (event.key === "Escape" && running) {
              event.preventDefault()
              runtime.composer().cancel()
            }
          }}
        />
        <div className="flex flex-wrap items-center gap-1.5">
          <ComposerPrimitive.AddAttachment
            aria-label="Attach images"
            className="rounded-full p-2 text-muted-foreground hover:bg-muted disabled:opacity-40"
          >
            <Plus className="size-4" />
          </ComposerPrimitive.AddAttachment>
          {!!options.models?.length && (
            <ModelPicker
              models={options.models}
              selection={options.selection ?? null}
              onSelectionChange={options.onSelectionChange}
              disabled={disabled}
              requireImageSupport={hasAttachments}
              triggerClassName="max-w-56 rounded-full px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted"
            />
          )}
          {options.onPlanModeChange && (
            <button
              type="button"
              disabled={disabled}
              aria-label="Plan mode"
              aria-pressed={!!options.planMode}
              onClick={() => options.onPlanModeChange?.(!options.planMode)}
              className={`flex items-center gap-1 rounded-full px-2 py-1.5 text-xs ${options.planMode ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted"}`}
            >
              <Map className="size-3.5" />
              Plan
            </button>
          )}
          {!!options.skills?.length && (
            <select
              aria-label="Insert skill"
              disabled={disabled}
              value=""
              onChange={(event) => insert(`/${event.target.value}`)}
              className="max-w-28 rounded-full bg-transparent p-1 text-xs text-muted-foreground"
            >
              <option value="" disabled>
                Skills
              </option>
              {options.skills.map((skill) => (
                <option key={skill.name} value={skill.name}>
                  {skill.name}
                </option>
              ))}
            </select>
          )}
          {!!options.mentionPaths?.length && (
            <select
              aria-label="Mention file"
              disabled={disabled}
              value=""
              onChange={(event) =>
                insert(serializeComposerFileLink(event.target.value))
              }
              className="max-w-28 rounded-full bg-transparent p-1 text-xs text-muted-foreground"
            >
              <option value="" disabled>
                Files
              </option>
              {options.mentionPaths.map((path) => (
                <option key={path} value={path}>
                  {path}
                </option>
              ))}
            </select>
          )}
          <div className="ml-auto flex items-center gap-2">
            <ContextWindowMeter
              usedTokens={options.contextUsage?.usedTokens}
              contextWindow={options.contextUsage?.contextWindow}
            />
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

export default function AssistantConversation({
  messages,
  threadId,
  composer,
  footer,
  isLoading,
  hydrationFailed,
  showPlanArtifact,
  queuedMessages = [],
  isStreaming,
  settingUpSandbox,
  onApprove,
  onReject,
  onAutoApprove,
  onOpenFile,
}: AssistantConversationProps) {
  const runtime = useAui()
  const { error, sending, failedMessages, dispatch, configureComposer } =
    useAuiState((s) => s.thread.extras) as ConversationRuntimeExtras
  useLayoutEffect(
    () => configureComposer(composer),
    [configureComposer, composer]
  )
  useLayoutEffect(() => () => configureComposer(undefined), [configureComposer])
  const visibleMessages = useMemo(
    () => messages.filter((message) => !message.hidden),
    [messages]
  )
  return (
    <>
      <ThreadPrimitive.Root
        className="relative flex min-h-0 min-w-0 flex-1 flex-col"
        data-testid="assistant-ui-conversation"
      >
        <ThreadPrimitive.Viewport
          turnAnchor="top"
          className="min-h-0 flex-1 [scrollbar-gutter:stable_both-edges] overflow-x-hidden overflow-y-auto"
          aria-label="Conversation messages"
        >
          <div className="mx-auto w-full max-w-3xl px-5 pt-6 pb-8 sm:px-8">
            {isLoading && !visibleMessages.length ? (
              <div
                role="status"
                className="py-16 text-center text-sm text-muted-foreground"
              >
                Loading conversation…
              </div>
            ) : (
              !visibleMessages.length &&
              !isStreaming && (
                <div className="py-16 text-center">
                  <h2 className="text-2xl font-medium tracking-tight">
                    What are we working on?
                  </h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Send a message to get started.
                  </p>
                </div>
              )
            )}
            {hydrationFailed && (
              <p role="alert" className="mb-4 text-sm text-destructive">
                This conversation could not be loaded. Reload to try again.
              </p>
            )}
            <ThreadPrimitive.Messages>
              {() => (
                <AssistantMessage
                  onApprove={onApprove}
                  onReject={onReject}
                  onAutoApprove={onAutoApprove}
                  onOpenFile={onOpenFile}
                />
              )}
            </ThreadPrimitive.Messages>
            {isStreaming && visibleMessages.at(-1)?.author !== "agent" && (
              <p
                role="status"
                className="animate-pulse text-sm text-muted-foreground"
              >
                {settingUpSandbox ? "Setting up sandbox…" : "Working…"}
              </p>
            )}
            {threadId && showPlanArtifact && (
              <InlinePlanArtifact threadId={threadId} />
            )}
            {queuedMessages.map((message) => (
              <div
                key={message.id}
                className="my-3 ml-auto max-w-[85%] rounded-2xl border border-dashed border-border px-4 py-3 text-sm"
              >
                <div className="mb-1 text-xs text-muted-foreground">
                  Queued next
                </div>
                <div className="whitespace-pre-wrap">{message.content}</div>
                {message.images?.length ? (
                  <div className="mt-1 text-xs text-muted-foreground">
                    {message.images.length} image(s) attached
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </ThreadPrimitive.Viewport>
        <div className="relative mx-auto w-full max-w-3xl px-4 pt-2 pb-4 sm:px-6">
          <ThreadPrimitive.ScrollToBottom
            aria-label="Scroll to bottom"
            className="absolute -top-10 left-1/2 rounded-full border border-border bg-background p-2 shadow-sm disabled:invisible"
          >
            <ArrowDown className="size-4" />
          </ThreadPrimitive.ScrollToBottom>
          {footer}
          {error && (
            <p role="alert" className="mb-2 px-2 text-sm text-destructive">
              {error}
            </p>
          )}
          {failedMessages.map(({ id, message }) => (
            <div
              key={id}
              className="mb-3 rounded-xl border border-destructive/30 p-3 text-sm"
            >
              <p className="whitespace-pre-wrap">
                {message.content
                  .filter((part) => part.type === "text")
                  .map((part) => part.text)
                  .join("\n")}
              </p>
              <button
                type="button"
                disabled={sending || composer.disabled}
                className="mt-2 underline"
                onClick={() => dispatch(message)}
              >
                Retry failed message
              </button>
            </div>
          ))}
          <Composer options={composer} runtime={runtime} />
        </div>
      </ThreadPrimitive.Root>
    </>
  )
}
