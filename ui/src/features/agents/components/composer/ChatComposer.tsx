import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ImagePlus, Plus, X } from "lucide-react"

import { ComposerCommandMenu } from "./ComposerCommandMenu"
import { ComposerControl } from "./ComposerControl"
import { ComposerPrimaryActions } from "./ComposerPrimaryActions"
import {
  ComposerPromptEditor,
  mentionReplacementText,
} from "./ComposerPromptEditor"
import { ContextWindowMeter } from "./ContextWindowMeter"
import { WorkspaceSelector } from "./WorkspaceSelector"
import {
  LocalBranchSelector,
  LocalRepoSelector,
  LocalWorkspaceSelector,
  RunTargetSelector,
} from "./RunTargetSelector"
import {
  COMPOSER_PATH_DRAG_MIME,
  detectComposerTrigger,
  replaceTextRange,
} from "./composerTrigger"
import type { ComposerCommandItem } from "./ComposerCommandMenu"
import type { ActiveRun } from "./ComposerPrimaryActions"
import type {
  ComposerCommandKey,
  ComposerPromptEditorHandle,
} from "./ComposerPromptEditor"
import type { ComposerSlashCommand, ComposerTrigger } from "./composerTrigger"
import type { RunTarget } from "./RunTargetSelector"
import type {
  DesktopProject,
  DesktopProjectRef,
  DesktopWorkspaceMode,
} from "@/desktop"
import type {
  FollowUpBehavior,
  ModelOption,
  Skill,
  WorkspaceOption,
} from "@/lib/api"
import type { ImageChunk } from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { ModelPicker } from "@/features/agents/components/ModelPicker"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import { useRegisterAppCommands } from "@/lib/appCommands"
import { cn } from "@/lib/utils"

export type { ActiveRun }

const MAX_IMAGE_COUNT = 5
const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const MAX_MENTION_SUGGESTIONS = 8
const SUPPORTED_IMAGE_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
])

interface SlashCommandSpec {
  command: ComposerSlashCommand
  label: string
  description: string
}

const SLASH_COMMANDS: Array<SlashCommandSpec> = [
  {
    command: "offload",
    label: "/offload",
    description: "Offload conversation context",
  },
  {
    command: "model",
    label: "/model",
    description: "Pick a model and reasoning effort",
  },
]

/** How a submission should be treated: as the configured default, or the opposite. */
export interface SubmitOptions {
  /** Set by ⌘↵ / Ctrl+Enter: do the opposite of the configured follow-up behavior. */
  alternate: boolean
}

/** Text and attachments handed back to the composer, e.g. a cancelled queued message. */
export interface RestoredDraft {
  /** Changes on every restore so the same content can come back twice. */
  key: number
  text: string
  images: Array<ImageChunk>
}

export interface ChatComposerProps {
  placeholder?: string
  autoFocus?: boolean
  compact?: boolean
  disabled?: boolean
  busy?: boolean
  canOffload?: boolean
  /** Enables the stop button for the thread's live run. */
  activeRun?: ActiveRun
  onStop?: () => void | Promise<void>
  onSubmit?: (
    value: string,
    images: Array<ImageChunk>,
    options?: SubmitOptions
  ) => void | Promise<void>
  /**
   * Enter on an empty composer while a run is live. The thread view uses it to
   * send the next queued message now.
   */
  onEmptySubmit?: () => void
  /** What a message sent while a run is live does; drives copy only. */
  followUpBehavior?: FollowUpBehavior
  /** Content to put back in front of whatever is being typed. */
  restoreDraft?: RestoredDraft | null
  models?: Array<ModelOption>
  selection?: ModelSelection | null
  onSelectionChange?: (next: ModelSelection | null) => void
  /** Repos the user can target. When provided with onRepoChange, a repo picker is shown. */
  repos?: Array<{ full_name: string }>
  autoSelectRepo?: boolean
  selectedRepo?: string | null
  onRepoChange?: (repo: string | null) => void
  /** Desktop-only execution target. Omit this prop to keep the control out of the web UI. */
  runTarget?: RunTarget
  onRunTargetChange?: (next: RunTarget) => void
  localRepos?: Array<DesktopProject>
  selectedLocalRepoPath?: string | null
  selectedLocalRepoBranch?: string | null
  localRepoBranches?: Array<DesktopProjectRef>
  localWorkspaceMode?: DesktopWorkspaceMode
  localWorktreeLabel?: string
  onLocalWorkspaceModeChange?: (next: DesktopWorkspaceMode) => void
  onSelectLocalRepo?: (cwd: string) => void
  onAddLocalRepo?: () => void
  onRemoveLocalRepo?: (cwd: string) => void
  onRefreshLocalRepoBranch?: () => void
  onSelectLocalRepoBranch?: (branch: string) => void
  /** Workspaces a new thread can boot from. The picker appears only when there are several. */
  workspaceOptions?: Array<WorkspaceOption>
  selectedWorkspace?: string | null
  onWorkspaceChange?: (slug: string | null) => void
  /** Paths offered by `@` autocomplete — in a thread, the files the agent has touched. */
  mentionPaths?: Array<string>
  skills?: Array<Skill>
  contextUsage?: {
    usedTokens?: number | null
    contextWindow?: number | null
  }
  /** Route/model the Auto router picked for the current run. */
  routed?: { route?: string; modelId?: string | null } | null
}

function fileToImageChunk(file: File): Promise<ImageChunk | null> {
  if (!SUPPORTED_IMAGE_TYPES.has(file.type) || file.size > MAX_IMAGE_BYTES) {
    return Promise.resolve(null)
  }

  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : ""
      const base64 = dataUrl.split(",")[1]
      resolve(
        base64
          ? { kind: "image", base64, mimeType: file.type, fileName: file.name }
          : null
      )
    }
    reader.onerror = () => resolve(null)
    reader.readAsDataURL(file)
  })
}

export function buildCommandItems(
  trigger: ComposerTrigger,
  mentionPaths: Array<string>,
  skills: Array<Skill>,
  includeModelCommand = true,
  includeOffloadCommand = false
): Array<ComposerCommandItem> {
  const query = trigger.query.toLowerCase()

  if (trigger.kind === "slash-command" || trigger.kind === "skill-command") {
    const skillItems = skills
      .filter((skill) => skill.name.startsWith(query))
      .map((skill) => ({
        id: `skill:${skill.name}`,
        type: "skill" as const,
        name: skill.name,
        label: `/${skill.name}`,
        description: skill.description,
      }))
    if (trigger.kind === "skill-command") return skillItems

    const skillNames = new Set(skills.map((skill) => skill.name))
    return [
      ...SLASH_COMMANDS.filter(
        (spec) =>
          spec.command.startsWith(query) &&
          !skillNames.has(spec.command) &&
          (includeModelCommand || spec.command !== "model") &&
          (includeOffloadCommand || spec.command !== "offload")
      ).map((spec) => ({
        id: `slash:${spec.command}`,
        type: "slash-command" as const,
        command: spec.command,
        label: spec.label,
        description: spec.description,
      })),
      ...skillItems,
    ]
  }

  return mentionPaths
    .filter((path) => !query || path.toLowerCase().includes(query))
    .slice(0, MAX_MENTION_SUGGESTIONS)
    .map((path) => ({
      id: `path:${path}`,
      type: "path" as const,
      path,
      label: path.slice(path.lastIndexOf("/") + 1),
      description: path,
    }))
}

/** Prompt editor with autocomplete, model selection, attachments, and send/stop controls. */
export const ChatComposer = memo(function ChatComposer({
  placeholder = "Ask Open SWE to build, fix bugs, explore",
  autoFocus = false,
  compact = false,
  disabled = false,
  busy = false,
  canOffload = false,
  activeRun,
  onStop,
  onSubmit,
  onEmptySubmit,
  followUpBehavior = "steer",
  restoreDraft = null,
  models = [],
  selection = null,
  onSelectionChange,
  repos,
  autoSelectRepo = true,
  selectedRepo = null,
  onRepoChange,
  runTarget,
  onRunTargetChange,
  localRepos = [],
  selectedLocalRepoPath = null,
  selectedLocalRepoBranch = null,
  localRepoBranches = [],
  localWorkspaceMode = "local",
  localWorktreeLabel,
  onLocalWorkspaceModeChange,
  onSelectLocalRepo,
  onAddLocalRepo,
  onRemoveLocalRepo,
  onRefreshLocalRepoBranch,
  onSelectLocalRepoBranch,
  workspaceOptions = [],
  selectedWorkspace = null,
  onWorkspaceChange,
  mentionPaths = [],
  skills = [],
  contextUsage,
  routed,
}: ChatComposerProps) {
  const [value, setValue] = useState("")
  const [cursor, setCursor] = useState(0)
  const [pendingImages, setPendingImages] = useState<Array<ImageChunk>>([])
  const [dragKind, setDragKind] = useState<"files" | "path" | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [activeItemId, setActiveItemId] = useState<string | null>(null)
  const [dismissedTriggerKey, setDismissedTriggerKey] = useState<string | null>(
    null
  )
  const [modelPickerOpen, setModelPickerOpen] = useState(false)
  const [extrasMenuOpen, setExtrasMenuOpen] = useState(false)
  const [composerError, setComposerError] = useState<string | null>(null)
  const composerShortcuts = useMemo(
    () => [
      {
        id: "composer-send",
        label: "Send message",
        shortcuts: ["enter"],
        group: "Composer",
        showInPalette: false,
      },
      {
        id: "composer-new-line",
        label: "New line",
        shortcuts: ["shift+enter"],
        group: "Composer",
        showInPalette: false,
      },
      {
        id: "open-model-picker",
        label: "Choose model",
        aliases: ["model picker", "change model", "reasoning effort"],
        shortcuts: ["mod+shift+m"],
        group: "Composer",
        run: () => setModelPickerOpen(true),
      },
      ...(activeRun?.running
        ? [
            {
              id: "composer-stop-run",
              label: "Stop active run",
              shortcuts: ["escape"],
              group: "Composer",
              showInPalette: false,
            },
          ]
        : []),
    ],
    [activeRun?.running]
  )
  useRegisterAppCommands(composerShortcuts)

  const editorRef = useRef<ComposerPromptEditorHandle | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dragDepthRef = useRef(0)

  useEffect(() => {
    if (autoFocus) editorRef.current?.focus()
  }, [autoFocus])

  // Synchronous double-submit guard: blocks a same-tick second send (Enter +
  // click, or two rapid Enters) before React re-renders. Scoped to the send
  // request only — never the run lifecycle.
  const submittingRef = useRef(false)

  const trigger = useMemo(
    () => detectComposerTrigger(value, cursor),
    [cursor, value]
  )
  const triggerKey = trigger ? `${trigger.kind}:${trigger.rangeStart}` : null
  const skillNames = useMemo(
    () => new Set(skills.map((skill) => skill.name)),
    [skills]
  )
  const commandItems = useMemo(
    () =>
      trigger
        ? buildCommandItems(
            trigger,
            mentionPaths,
            skills,
            models.length > 0,
            canOffload
          )
        : [],
    [mentionPaths, models.length, skills, trigger, canOffload]
  )
  const menuOpen =
    trigger !== null &&
    commandItems.length > 0 &&
    dismissedTriggerKey !== triggerKey
  const activeItem =
    commandItems.find((item) => item.id === activeItemId) ??
    commandItems[0] ??
    null

  const selectedModelSupportsImages = useMemo(() => {
    if (!selection || pendingImages.length === 0) return true
    return models.some((m) => m.id === selection.modelId && m.supports_images)
  }, [models, pendingImages.length, selection])

  const composerEmpty = value.trim().length === 0 && pendingImages.length === 0
  const canSubmit =
    !disabled && !isSubmitting && selectedModelSupportsImages && !composerEmpty

  const applyPrompt = useCallback((nextValue: string, nextCursor: number) => {
    setValue(nextValue)
    setCursor(nextCursor)
    setDismissedTriggerKey(null)
    setActiveItemId(null)
  }, [])

  const restoredKeyRef = useRef<number | null>(null)
  useEffect(() => {
    if (!restoreDraft || restoredKeyRef.current === restoreDraft.key) return
    restoredKeyRef.current = restoreDraft.key
    const current = editorRef.current?.readSnapshot()?.value ?? value
    const next = [restoreDraft.text, current]
      .filter((part) => part.trim().length > 0)
      .join("\n\n")
    applyPrompt(next, next.length)
    if (restoreDraft.images.length > 0) {
      // oxlint-disable-next-line react/set-state-in-effect
      setPendingImages((prev) => [...restoreDraft.images, ...prev])
    }
    editorRef.current?.focusAtEnd()
  }, [applyPrompt, restoreDraft, value])

  const handleSubmit = useCallback(
    async (options?: SubmitOptions) => {
      if (submittingRef.current || disabled) return
      // The editor is the source of truth for what is on screen; a keystroke that
      // has not yet round-tripped through state would otherwise be dropped.
      const snapshot = editorRef.current?.readSnapshot()
      const trimmed = (snapshot?.value ?? value).trim()
      if (trimmed.length === 0 && pendingImages.length === 0) return

      if (trimmed === "/offload" && (!canOffload || pendingImages.length)) {
        setComposerError(
          pendingImages.length
            ? "Offloading does not accept attachments."
            : "Offloading requires an idle, existing conversation."
        )
        return
      }

      const images = pendingImages
      submittingRef.current = true
      setIsSubmitting(true)
      applyPrompt("", 0)
      setPendingImages([])
      setComposerError(null)
      try {
        await onSubmit?.(trimmed, images, options)
      } catch {
        // Caller surfaces send errors (e.g. via react-query mutation state).
      } finally {
        submittingRef.current = false
        setIsSubmitting(false)
      }
    },
    [applyPrompt, disabled, onSubmit, pendingImages, value, canOffload]
  )

  const selectCommandItem = useCallback(
    (item: ComposerCommandItem) => {
      if (!trigger) return

      if (item.type === "slash-command" && item.command === "offload") {
        const next = replaceTextRange(
          value,
          trigger.rangeStart,
          trigger.rangeEnd,
          "/offload "
        )
        applyPrompt(next.text, next.cursor)
        return
      }

      if (item.type === "path" || item.type === "skill") {
        const next = replaceTextRange(
          value,
          trigger.rangeStart,
          trigger.rangeEnd,
          item.type === "path"
            ? mentionReplacementText(item.path)
            : `/${item.name} `
        )
        applyPrompt(next.text, next.cursor)
        return
      }

      // Slash commands are settings, not prose: they act and then erase
      // themselves rather than being sent to the agent.
      const next = replaceTextRange(
        value,
        trigger.rangeStart,
        trigger.rangeEnd,
        ""
      )
      applyPrompt(next.text, next.cursor)
      if (item.command === "model") setModelPickerOpen(true)
    },
    [applyPrompt, trigger, value]
  )

  const handleCommandKeyDown = useCallback(
    (key: ComposerCommandKey, event: KeyboardEvent): boolean => {
      if (menuOpen && activeItem) {
        switch (key) {
          case "ArrowDown":
          case "ArrowUp": {
            const index = commandItems.findIndex(
              (item) => item.id === activeItem.id
            )
            const step = key === "ArrowDown" ? 1 : -1
            const next =
              commandItems[
                (index + step + commandItems.length) % commandItems.length
              ]
            if (next) setActiveItemId(next.id)
            return true
          }
          case "Enter":
          case "Tab":
            selectCommandItem(activeItem)
            return true
          case "Escape":
            setDismissedTriggerKey(triggerKey)
            return true
        }
      }

      if (key === "Enter" && !event.shiftKey) {
        if (canSubmit) {
          void handleSubmit({ alternate: event.metaKey || event.ctrlKey })
        } else if (
          composerEmpty &&
          busy &&
          !disabled &&
          !isSubmitting &&
          onEmptySubmit
        ) {
          // Only a truly empty composer sends the queue head. A draft that
          // cannot be sent (images on a text-only model) must stay put.
          onEmptySubmit()
        }
        // Swallow it either way: a bare Enter must never insert a newline in a
        // composer whose Enter means "send".
        return true
      }
      return false
    },
    [
      activeItem,
      busy,
      canSubmit,
      commandItems,
      composerEmpty,
      disabled,
      handleSubmit,
      isSubmitting,
      menuOpen,
      onEmptySubmit,
      selectCommandItem,
      triggerKey,
    ]
  )

  const addFiles = useCallback(async (files: FileList | Array<File>) => {
    const nextImages = await Promise.all(
      Array.from(files).map(fileToImageChunk)
    )
    const validImages = nextImages.filter(
      (image): image is ImageChunk => image !== null
    )
    if (validImages.length === 0) return
    setPendingImages((prev) =>
      [...prev, ...validImages].slice(0, MAX_IMAGE_COUNT)
    )
  }, [])

  const handleFileChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      if (event.target.files) void addFiles(event.target.files)
      event.target.value = ""
    },
    [addFiles]
  )

  const insertMentionAtEnd = useCallback(
    (path: string) => {
      const separator = value.length === 0 || /\s$/.test(value) ? "" : " "
      const nextValue = `${value}${separator}${mentionReplacementText(path)}`
      applyPrompt(nextValue, nextValue.length)
      editorRef.current?.focusAtEnd()
    },
    [applyPrompt, value]
  )

  // Two accepted payloads: OS image files, and a repo path dragged out of the
  // changed-files list. The drop only lands if dragover is also prevented, so
  // every handler has to agree on what it accepts.
  const dragKindOf = (
    event: React.DragEvent<HTMLDivElement>
  ): "files" | "path" | null => {
    if (event.dataTransfer.types.includes(COMPOSER_PATH_DRAG_MIME))
      return "path"
    if (event.dataTransfer.types.includes("Files")) return "files"
    return null
  }

  const handleDragEnter = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      const kind = dragKindOf(event)
      if (!kind) return
      event.preventDefault()
      dragDepthRef.current += 1
      setDragKind(kind)
    },
    []
  )

  const handleDragOver = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      const kind = dragKindOf(event)
      if (!kind) return
      event.preventDefault()
      setDragKind(kind)
    },
    []
  )

  const handleDragLeave = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      if (!dragKindOf(event)) return
      event.preventDefault()
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
      if (dragDepthRef.current === 0) setDragKind(null)
    },
    []
  )

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      const droppedPath = event.dataTransfer.getData(COMPOSER_PATH_DRAG_MIME)
      if (droppedPath) {
        event.preventDefault()
        dragDepthRef.current = 0
        setDragKind(null)
        insertMentionAtEnd(droppedPath)
        return
      }
      if (!event.dataTransfer.types.includes("Files")) return
      event.preventDefault()
      dragDepthRef.current = 0
      setDragKind(null)
      void addFiles(event.dataTransfer.files)
    },
    [addFiles, insertMentionAtEnd]
  )

  const handlePaste = useCallback(
    (event: React.ClipboardEvent<HTMLElement>) => {
      const files: Array<File> = []
      for (const item of Array.from(event.clipboardData.items)) {
        if (item.kind !== "file") continue
        const file = item.getAsFile()
        if (file && SUPPORTED_IMAGE_TYPES.has(file.type)) files.push(file)
      }
      if (files.length === 0) return
      event.preventDefault()
      void addFiles(files)
    },
    [addFiles]
  )

  return (
    <div
      className={cn(
        "relative w-full font-sans text-[13px]",
        compact ? "max-w-none" : "max-w-2xl"
      )}
    >
      {composerError && (
        <div className="mb-2 px-1 text-xs text-destructive" role="alert">
          {composerError}
        </div>
      )}

      {!selectedModelSupportsImages && (
        <div className="dropdown-glass mb-2 rounded-xl border border-warning/30 px-3 py-2 text-xs text-muted-foreground">
          The selected model does not accept image input. Remove the image
          {pendingImages.length > 1 ? "s" : ""} or switch to a vision-enabled
          model to send.
        </div>
      )}

      {(onRepoChange ||
        onRunTargetChange ||
        onWorkspaceChange ||
        (runTarget === "local" && onSelectLocalRepoBranch)) && (
        <div className="relative mx-5 -mb-3 flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2 rounded-t-2xl bg-accent px-4 pt-3 pb-5 text-xs dark:bg-muted">
          {runTarget && onRunTargetChange && (
            <RunTargetSelector onChange={onRunTargetChange} value={runTarget} />
          )}
          {runTarget !== "local" && onWorkspaceChange && (
            <WorkspaceSelector
              workspaces={workspaceOptions}
              selectedSlug={selectedWorkspace}
              onChange={onWorkspaceChange}
            />
          )}
          {runTarget !== "local" && onRepoChange && (
            <RepoSelector
              emptySelectionLabel="Don't work in a repository"
              noMatchesLabel="No matching repositories"
              onRepoChange={onRepoChange}
              placeholder="Select repository"
              repos={repos}
              searchPlaceholder="Search repositories…"
              autoSelect={autoSelectRepo}
              selectedRepo={selectedRepo}
              side="top"
            />
          )}
          {runTarget === "local" &&
            onSelectLocalRepo &&
            onAddLocalRepo &&
            onRemoveLocalRepo && (
              <LocalRepoSelector
                onAddRepo={onAddLocalRepo}
                onRemoveRepo={onRemoveLocalRepo}
                onSelectRepo={onSelectLocalRepo}
                repos={localRepos}
                selectedRepoPath={selectedLocalRepoPath}
                side="top"
              />
            )}
          {runTarget === "local" &&
            onRefreshLocalRepoBranch &&
            onSelectLocalRepoBranch && (
              <LocalBranchSelector
                refs={localRepoBranches}
                disabled={!selectedLocalRepoPath}
                onRefresh={onRefreshLocalRepoBranch}
                onSelectBranch={onSelectLocalRepoBranch}
                selectedBranch={selectedLocalRepoBranch}
              />
            )}
          {runTarget === "local" && onSelectLocalRepoBranch && (
            <LocalWorkspaceSelector
              onChange={onLocalWorkspaceModeChange}
              value={localWorkspaceMode}
              worktreeLabel={localWorktreeLabel}
            />
          )}
        </div>
      )}

      <div
        className={cn(
          "relative z-10 flex flex-col rounded-2xl border border-foreground/20 bg-card px-3 py-2.5 shadow-md transition-[border-color,box-shadow] duration-300 hover:shadow-lg dark:bg-[#222]",
          compact ? "min-h-[88px]" : "min-h-[106px]",
          dragKind
            ? "border-primary"
            : "focus-within:border-foreground/30 hover:border-foreground/30"
        )}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        {menuOpen && (
          <ComposerCommandMenu
            activeItemId={activeItem?.id ?? null}
            items={commandItems}
            onHighlight={setActiveItemId}
            onSelect={selectCommandItem}
            triggerKind={trigger.kind}
          />
        )}

        {dragKind && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-2xl bg-card/80 backdrop-blur-sm">
            <span className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground">
              {dragKind === "path"
                ? "Drop to mention this file"
                : "Drop images here"}
            </span>
          </div>
        )}

        <input
          accept="image/png,image/jpeg,image/gif,image/webp"
          className="hidden"
          multiple
          onChange={handleFileChange}
          ref={fileInputRef}
          type="file"
        />

        {pendingImages.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {pendingImages.map((image, index) => (
              <div
                className="group relative"
                key={`${image.fileName ?? "image"}-${index}`}
              >
                <img
                  alt={image.fileName || "Pending image"}
                  className="size-16 rounded-lg border border-border object-cover"
                  src={`data:${image.mimeType};base64,${image.base64}`}
                />
                <button
                  aria-label="Remove image"
                  className="absolute -top-1.5 -right-1.5 flex size-5 items-center justify-center rounded-full border border-border bg-card text-muted-foreground opacity-0 shadow-sm transition-opacity group-hover:opacity-100 hover:text-foreground"
                  onClick={() =>
                    setPendingImages((prev) =>
                      prev.filter((_, i) => i !== index)
                    )
                  }
                  type="button"
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        <ComposerPromptEditor
          className={compact ? "min-h-[36px]" : "min-h-[52px]"}
          cursor={cursor}
          disabled={disabled}
          editorRef={editorRef}
          onChange={(nextValue, nextCursor) => {
            setValue(nextValue)
            setCursor(nextCursor)
          }}
          onCommandKeyDown={handleCommandKeyDown}
          onPaste={handlePaste}
          placeholder={
            busy
              ? followUpBehavior === "steer"
                ? "Send a message to steer the run..."
                : "Send a message to queue next..."
              : placeholder
          }
          skillNames={skillNames}
          value={value}
        />

        <div className="mt-auto grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto_auto] items-end gap-1 pt-2 text-xs text-muted-foreground">
          <Menu onOpenChange={setExtrasMenuOpen}>
            <MenuTrigger
              render={
                <ComposerControl
                  aria-label="More composer options"
                  className="size-7 px-0"
                  type="button"
                />
              }
            >
              <Plus className="size-4" />
            </MenuTrigger>
            <MenuPopup align="start" className="w-44" side="top" sideOffset={7}>
              <MenuItem
                disabled={disabled || pendingImages.length >= MAX_IMAGE_COUNT}
                onClick={() => fileInputRef.current?.click()}
              >
                <ImagePlus />
                Attach images
              </MenuItem>
            </MenuPopup>
          </Menu>

          <div className="flex min-w-0 flex-wrap items-center gap-1">
            {models.length > 0 && (
              <ModelPicker
                models={models}
                onOpenChange={setModelPickerOpen}
                onSelectionChange={onSelectionChange}
                open={modelPickerOpen}
                requireImageSupport={pendingImages.length > 0}
                routed={routed}
                selection={selection}
                triggerClassName="h-7 max-w-full rounded-md px-2 text-xs/relaxed text-muted-foreground/70 hover:bg-muted hover:text-foreground/80"
              />
            )}
          </div>

          <div className="flex items-center gap-1">
            <ContextWindowMeter
              contextWindow={contextUsage?.contextWindow}
              usedTokens={contextUsage?.usedTokens}
            />
          </div>

          <ComposerPrimaryActions
            activeRun={activeRun}
            canSubmit={canSubmit}
            onSubmit={() => void handleSubmit()}
            runningLabel={
              followUpBehavior === "steer" ? "Steer agent" : "Queue message"
            }
            onStop={onStop}
            stopOnEscape={!menuOpen && !modelPickerOpen && !extrasMenuOpen}
            submitting={isSubmitting}
          />
        </div>
      </div>
    </div>
  )
})
