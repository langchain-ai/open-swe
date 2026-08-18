import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ImagePlus,
  LoaderCircle,
  Map as MapIcon,
  Mic,
  Plus,
  ServerCog as ServerCogIcon,
  Square,
  X,
} from "lucide-react"

import { ComposerCommandMenu } from "./ComposerCommandMenu"
import { ComposerControl, ComposerControlIcon } from "./ComposerControl"
import { ComposerPrimaryActions } from "./ComposerPrimaryActions"
import {
  ComposerPromptEditor,
  mentionReplacementText,
} from "./ComposerPromptEditor"
import { ContextWindowMeter } from "./ContextWindowMeter"
import { EnvironmentSelector } from "./EnvironmentSelector"
import { RunTargetSelector } from "./RunTargetSelector"
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
import type { DesktopProject } from "@/desktop"
import type { EnvironmentOption, ModelOption, Skill } from "@/lib/api"
import type { ImageChunk } from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { ModelPicker } from "@/features/agents/components/ModelPicker"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { transcribeAudio } from "@/lib/api"
import { cn } from "@/lib/utils"

export type { ActiveRun }

const MAX_IMAGE_COUNT = 5
const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const MAX_AUDIO_BYTES = 10 * 1024 * 1024
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
    command: "plan",
    label: "/plan",
    description: "Research read-only and propose a plan first",
  },
  {
    command: "default",
    label: "/default",
    description: "Leave plan mode and edit directly",
  },
  {
    command: "model",
    label: "/model",
    description: "Pick a model and reasoning effort",
  },
]

export interface ChatComposerProps {
  placeholder?: string
  autoFocus?: boolean
  compact?: boolean
  disabled?: boolean
  busy?: boolean
  /** Enables the stop button for the thread's live run. */
  activeRun?: ActiveRun
  onStop?: () => void | Promise<void>
  onSubmit?: (value: string, images: Array<ImageChunk>) => void | Promise<void>
  models?: Array<ModelOption>
  selection?: ModelSelection | null
  onSelectionChange?: (next: ModelSelection) => void
  /** Repos the user can target. When provided with onRepoChange, a repo picker is shown. */
  repos?: Array<{ full_name: string }>
  selectedRepo?: string | null
  onRepoChange?: (repo: string | null) => void
  /** Desktop-only execution target. Omit this prop to keep the control out of the web UI. */
  runTarget?: RunTarget
  onRunTargetChange?: (next: RunTarget) => void
  localProjects?: Array<DesktopProject>
  selectedLocalProjectPath?: string | null
  selectedLocalProjectBranch?: string | null
  onSelectLocalProject?: (cwd: string) => void
  onAddLocalProject?: () => void
  onRemoveLocalProject?: (cwd: string) => void
  onRefreshLocalProjectBranch?: () => void
  /** When provided, a Plan mode toggle is shown. Plan mode researches read-only and proposes a plan before editing. */
  planMode?: boolean
  onPlanModeChange?: (next: boolean) => void
  /** Admins only: when provided, an Admin toggle is shown. Admin threads can manage environments. */
  adminThread?: boolean
  onAdminThreadChange?: (next: boolean) => void
  /** Environments a new thread can boot from. The picker appears only when there are several. */
  environments?: Array<EnvironmentOption>
  selectedEnvironment?: string | null
  onEnvironmentChange?: (slug: string | null) => void
  /** Paths offered by `@` autocomplete — in a thread, the files the agent has touched. */
  mentionPaths?: Array<string>
  skills?: Array<Skill>
  contextUsage?: {
    usedTokens?: number | null
    contextWindow?: number | null
  }
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
  includeModelCommand = true
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
          (includeModelCommand || spec.command !== "model")
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

/**
 * The prompt composer: a Lexical editor with `@file` chips, `/command`
 * autocomplete, and `$skill` autocomplete, plus the control row (model, plan
 * mode, attachments, context)
 * and the send/stop button.
 */
export const ChatComposer = memo(function ChatComposer({
  placeholder = "Ask Open SWE to build, fix bugs, explore",
  autoFocus = false,
  compact = false,
  disabled = false,
  busy = false,
  activeRun,
  onStop,
  onSubmit,
  models = [],
  selection = null,
  onSelectionChange,
  repos,
  selectedRepo = null,
  onRepoChange,
  runTarget,
  onRunTargetChange,
  localProjects = [],
  selectedLocalProjectPath = null,
  selectedLocalProjectBranch = null,
  onSelectLocalProject,
  onAddLocalProject,
  onRemoveLocalProject,
  onRefreshLocalProjectBranch,
  planMode = false,
  onPlanModeChange,
  adminThread = false,
  onAdminThreadChange,
  environments = [],
  selectedEnvironment = null,
  onEnvironmentChange,
  mentionPaths = [],
  skills = [],
  contextUsage,
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
  const [dictationState, setDictationState] = useState<
    "idle" | "recording" | "transcribing"
  >("idle")
  const [dictationError, setDictationError] = useState<string | null>(null)

  const editorRef = useRef<ComposerPromptEditorHandle | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dragDepthRef = useRef(0)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Array<Blob>>([])
  const mountedRef = useRef(true)
  const requestingMicrophoneRef = useRef(false)

  useEffect(() => {
    if (autoFocus) editorRef.current?.focus()
  }, [autoFocus])

  // Synchronous double-submit guard: blocks a same-tick second send (Enter +
  // click, or two rapid Enters) before React re-renders. Scoped to the send
  // request only — never the run lifecycle.
  const submittingRef = useRef(false)

  useEffect(
    () => () => {
      mountedRef.current = false
      recorderRef.current?.stream.getTracks().forEach((track) => track.stop())
    },
    []
  )

  const trigger = useMemo(
    () => detectComposerTrigger(value, cursor),
    [cursor, value]
  )
  const triggerKey = trigger ? `${trigger.kind}:${trigger.rangeStart}` : null
  const commandItems = useMemo(
    () =>
      trigger
        ? buildCommandItems(trigger, mentionPaths, skills, models.length > 0)
        : [],
    [mentionPaths, models.length, skills, trigger]
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

  const canSubmit =
    !disabled &&
    !isSubmitting &&
    selectedModelSupportsImages &&
    (value.trim().length > 0 || pendingImages.length > 0)

  const applyPrompt = useCallback((nextValue: string, nextCursor: number) => {
    setValue(nextValue)
    setCursor(nextCursor)
    setDismissedTriggerKey(null)
    setActiveItemId(null)
  }, [])

  const handleDictation = useCallback(async () => {
    if (dictationState === "recording") {
      recorderRef.current?.stop()
      return
    }
    if (dictationState !== "idle" || requestingMicrophoneRef.current) return
    requestingMicrophoneRef.current = true
    setDictationError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      const mimeType = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4",
      ].find((type) => MediaRecorder.isTypeSupported(type))
      if (!mimeType) {
        stream.getTracks().forEach((track) => track.stop())
        throw new Error("Audio recording is not supported")
      }
      const recorder = new MediaRecorder(stream, { mimeType })
      recorderRef.current = recorder
      audioChunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size) audioChunksRef.current.push(event.data)
      }
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop())
        if (!mountedRef.current) return
        setDictationState("transcribing")
        try {
          const audio = new Blob(audioChunksRef.current, {
            type: recorder.mimeType,
          })
          if (!audio.size) throw new Error("No audio was recorded")
          if (audio.size > MAX_AUDIO_BYTES)
            throw new Error("Recording is too long")
          const transcript = await transcribeAudio(audio)
          const snapshot = editorRef.current?.readSnapshot() ?? {
            value,
            cursor: value.length,
          }
          const separator =
            snapshot.value && !/\s$/.test(snapshot.value) ? " " : ""
          const next = `${snapshot.value}${separator}${transcript}`
          applyPrompt(next, next.length)
          queueMicrotask(() => editorRef.current?.focusAtEnd())
        } catch (error) {
          setDictationError(
            error instanceof Error
              ? error.message
              : "Voice transcription failed"
          )
        } finally {
          recorderRef.current = null
          setDictationState("idle")
        }
      }
      recorder.start()
      setDictationState("recording")
    } catch (error) {
      setDictationError(
        error instanceof Error ? error.message : "Microphone access failed"
      )
    } finally {
      requestingMicrophoneRef.current = false
    }
  }, [applyPrompt, dictationState, value])

  const handleSubmit = useCallback(async () => {
    if (submittingRef.current || disabled) return
    // The editor is the source of truth for what is on screen; a keystroke that
    // has not yet round-tripped through state would otherwise be dropped.
    const snapshot = editorRef.current?.readSnapshot()
    const trimmed = (snapshot?.value ?? value).trim()
    if (trimmed.length === 0 && pendingImages.length === 0) return

    const images = pendingImages
    submittingRef.current = true
    setIsSubmitting(true)
    applyPrompt("", 0)
    setPendingImages([])
    try {
      await onSubmit?.(trimmed, images)
    } catch {
      // Caller surfaces send errors (e.g. via react-query mutation state).
    } finally {
      submittingRef.current = false
      setIsSubmitting(false)
    }
  }, [applyPrompt, disabled, onSubmit, pendingImages, value])

  const selectCommandItem = useCallback(
    (item: ComposerCommandItem) => {
      if (!trigger) return

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
      if (item.command === "plan") onPlanModeChange?.(true)
      if (item.command === "default") onPlanModeChange?.(false)
      if (item.command === "model") setModelPickerOpen(true)
    },
    [applyPrompt, onPlanModeChange, trigger, value]
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

      if (key === "Tab" && event.shiftKey && onPlanModeChange) {
        onPlanModeChange(!planMode)
        return true
      }
      if (key === "Enter" && !event.shiftKey) {
        if (canSubmit) void handleSubmit()
        // Swallow it either way: a bare Enter must never insert a newline in a
        // composer whose Enter means "send".
        return true
      }
      return false
    },
    [
      activeItem,
      canSubmit,
      commandItems,
      handleSubmit,
      menuOpen,
      onPlanModeChange,
      planMode,
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
      {(onRepoChange || onRunTargetChange || onEnvironmentChange) && (
        <div className="mb-2 flex min-w-0 flex-wrap items-center gap-2 px-1 text-xs">
          {runTarget !== "local" && onRepoChange && (
            <RepoSelector
              repos={repos}
              selectedRepo={selectedRepo}
              onRepoChange={onRepoChange}
            />
          )}
          {runTarget !== "local" && onEnvironmentChange && (
            <EnvironmentSelector
              environments={environments}
              selectedSlug={selectedEnvironment}
              onChange={onEnvironmentChange}
            />
          )}
          {runTarget &&
            onRunTargetChange &&
            onSelectLocalProject &&
            onAddLocalProject &&
            onRemoveLocalProject &&
            onRefreshLocalProjectBranch && (
              <RunTargetSelector
                localEnabled={Boolean(window.openSweDesktop)}
                onChange={onRunTargetChange}
                onAddProject={onAddLocalProject}
                onRemoveProject={onRemoveLocalProject}
                onRefreshBranch={onRefreshLocalProjectBranch}
                onSelectProject={onSelectLocalProject}
                projects={localProjects}
                selectedProjectPath={selectedLocalProjectPath}
                selectedProjectBranch={selectedLocalProjectBranch}
                value={runTarget}
              />
            )}
        </div>
      )}

      {dictationError && (
        <div className="mb-2 px-1 text-xs text-destructive" role="alert">
          {dictationError}
        </div>
      )}

      {!selectedModelSupportsImages && (
        <div className="dropdown-glass mb-2 rounded-xl border border-warning/30 px-3 py-2 text-xs text-muted-foreground">
          The selected model does not accept image input. Remove the image
          {pendingImages.length > 1 ? "s" : ""} or switch to a vision-enabled
          model to send.
        </div>
      )}

      <div
        className={cn(
          "relative flex flex-col rounded-2xl border border-border bg-card px-3 py-2.5 shadow-sm transition-colors",
          compact ? "min-h-[88px]" : "min-h-[106px]",
          dragKind && "border-primary"
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
            busy ? "Steer the agent after its current step..." : placeholder
          }
          value={value}
        />

        <div className="mt-auto grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto_auto] items-end gap-1 pt-2 text-xs text-muted-foreground">
          <Menu onOpenChange={setExtrasMenuOpen}>
            <MenuTrigger
              render={
                <ComposerControl
                  aria-label="More composer options"
                  className={cn(
                    "size-7 px-0",
                    adminThread &&
                      "bg-destructive/10 text-foreground hover:bg-destructive/10 hover:text-foreground"
                  )}
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
              {onPlanModeChange && (
                <MenuItem onClick={() => onPlanModeChange(!planMode)}>
                  <MapIcon />
                  {planMode ? "Disable plan mode" : "Enable plan mode"}
                </MenuItem>
              )}
              {onAdminThreadChange && (
                <MenuItem onClick={() => onAdminThreadChange(!adminThread)}>
                  <ServerCogIcon />
                  {adminThread ? "Disable admin mode" : "Enable admin mode"}
                </MenuItem>
              )}
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
                selection={selection}
                triggerClassName="h-7 max-w-full rounded-md px-2 text-xs/relaxed text-muted-foreground/70 hover:bg-muted hover:text-foreground/80"
              />
            )}

            {planMode && onPlanModeChange && (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <ComposerControl
                      aria-label="Exit plan mode"
                      aria-pressed
                      className="bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary"
                      onClick={() => onPlanModeChange(false)}
                      type="button"
                    />
                  }
                >
                  <ComposerControlIcon icon={MapIcon} />
                  <span>Plan</span>
                </TooltipTrigger>
                <TooltipPopup side="top">Exit plan mode</TooltipPopup>
              </Tooltip>
            )}
          </div>

          <div className="flex items-center gap-1">
            <ContextWindowMeter
              contextWindow={contextUsage?.contextWindow}
              usedTokens={contextUsage?.usedTokens}
            />

            {typeof navigator !== "undefined" &&
              "mediaDevices" in navigator &&
              typeof MediaRecorder !== "undefined" && (
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <ComposerControl
                        aria-label={
                          dictationState === "recording"
                            ? "Stop dictation"
                            : "Start dictation"
                        }
                        aria-pressed={dictationState === "recording"}
                        className={cn(
                          "size-7 px-0",
                          dictationState === "recording" && "text-destructive"
                        )}
                        disabled={disabled || dictationState === "transcribing"}
                        onClick={() => void handleDictation()}
                        type="button"
                      />
                    }
                  >
                    {dictationState === "transcribing" ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : dictationState === "recording" ? (
                      <Square className="size-3 fill-current" />
                    ) : (
                      <Mic className="size-4" />
                    )}
                  </TooltipTrigger>
                  <TooltipPopup side="top">
                    {dictationState === "recording"
                      ? "Stop dictation"
                      : "Dictate message"}
                  </TooltipPopup>
                </Tooltip>
              )}
          </div>

          <ComposerPrimaryActions
            activeRun={activeRun}
            canSubmit={canSubmit}
            onSubmit={() => void handleSubmit()}
            onStop={onStop}
            stopOnEscape={!menuOpen && !modelPickerOpen && !extrasMenuOpen}
            submitting={isSubmitting}
          />
        </div>
      </div>
    </div>
  )
})
