import { useQuery } from "@tanstack/react-query"
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ChatComposer as MacawChatComposer,
  ChatComposerAttachment,
  ChatComposerAttachmentMenu,
  ChatComposerAttachments,
  ChatComposerToolbar,
} from "@langchain/macaw-components/ChatComposer"

import { ComposerCommandMenu } from "./ComposerCommandMenu"
import { FuzzySearch } from "@/lib/fuzzySearch"
import { ComposerReplyCards } from "./ComposerReplyCards"
import type { PendingReply } from "./ComposerReplyCards"
import { ComposerPrimaryAction, useComposerRun } from "./ComposerPrimaryActions"
import {
  ComposerPromptEditor,
  mentionReplacementText,
} from "./ComposerPromptEditor"
import {
  ContextWindowMeter,
  type ContextWindowMeterProps,
} from "./ContextWindowMeter"
import { WorkspaceSelector } from "./WorkspaceSelector"
import { ProjectSelector } from "@/features/projects/components/ProjectSelector"
import type { Project } from "@/features/projects/lib/projects"
import {
  LocalBranchSelector,
  LocalProjectSelector,
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
  SessionUser,
  Skill,
  WorkspaceOption,
} from "@/lib/api"
import type { ImageChunk } from "@/features/agents/lib/types"
import type { PassageReply } from "@/features/agents/utils/passageReply"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { ModelPicker } from "@/features/agents/components/ModelPicker"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import { GitHubLinkRepoSelector } from "./GitHubLinkRepoSelector"
import { api } from "@/lib/api"
import type { SlackChannelOption, SlackUserOption } from "@/lib/api"
import { useRegisterAppCommands } from "@/lib/appCommands"
import {
  rankSlackChannels,
  rememberSlackChannels,
  slackChannelReference,
  useRecentSlackChannels,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import { cn } from "@/lib/utils"

export type { ActiveRun }

export const MAX_IMAGE_BYTES = 10 * 1024 * 1024
export const MAX_IMAGE_BATCH_BASE64 = 5 * Math.ceil(MAX_IMAGE_BYTES / 3) * 4
const MAX_FILE_BATCH_BASE64 = 10 * Math.ceil(MAX_IMAGE_BYTES / 3) * 4
const MAX_MENTION_SUGGESTIONS = 8
/** File types sent as they are; any other non-image file is sent as opaque bytes. */
export const SUPPORTED_FILE_TYPES: ReadonlySet<string> = new Set([
  "application/pdf",
  "text/plain",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
])

export const SUPPORTED_IMAGE_TYPES: ReadonlySet<string> = new Set([
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

/** What the person sends: their text, and what rides along with it. */
export interface ComposerSubmission {
  text: string
  images: Array<ImageChunk>
  replies: Array<PassageReply>
}

/** Text and attachments handed back to the composer, e.g. a cancelled queued message. */
export interface RestoredDraft {
  /** Changes on every restore so the same content can come back twice. */
  key: number
  text: string
  images: Array<ImageChunk>
  replies?: Array<PassageReply>
  /** Replace the previous suggested prompt if it is still exactly as suggested, instead of stacking on it. */
  replacesSuggestion?: boolean
}

/** The last suggested prompt put in a composer, which the next suggestion replaces while it is untouched. */
interface Suggestion {
  text: string
  images: ReadonlyArray<ImageChunk>
}

class ComposerDrafts {
  private drafts = new Map<
    string,
    {
      text: string
      images: Array<ImageChunk>
      replies: Array<PendingReply>
    }
  >()
  private suggestions = new Map<string, Suggestion>()

  read(key?: string) {
    return key ? this.drafts.get(key) : undefined
  }

  write(
    key: string | undefined,
    text: string,
    images: Array<ImageChunk>,
    replies: Array<PendingReply>
  ) {
    if (!key) return
    this.drafts.delete(key)
    if (!text && !images.length && !replies.length) {
      this.suggestions.delete(key)
      return
    }
    this.drafts.set(key, { text, images, replies })
    if (this.drafts.size > 50) {
      const oldest = this.drafts.keys().next().value
      if (oldest) {
        this.drafts.delete(oldest)
        this.suggestions.delete(oldest)
      }
    }
  }

  suggestion(key?: string) {
    return key ? this.suggestions.get(key) : undefined
  }

  suggest(key: string | undefined, suggestion: Suggestion) {
    if (key) this.suggestions.set(key, suggestion)
  }
}

const composerDrafts = new ComposerDrafts()

export interface ChatComposerProps {
  draftKey?: string
  placeholder?: string
  autoFocus?: boolean
  compact?: boolean
  disabled?: boolean
  busy?: boolean
  canOffload?: boolean
  /** Enables the stop button for the thread's live run. */
  activeRun?: ActiveRun
  onStop?: () => void | Promise<void>
  /** False when this viewer may not stop the live run. */
  stoppable?: boolean
  onSubmit?: (
    submission: ComposerSubmission,
    options?: SubmitOptions
  ) => void | Promise<void>
  /** What a message sent while a run is live does; drives copy only. */
  followUpBehavior?: FollowUpBehavior
  /** Content to put back in front of whatever is being typed. */
  restoreDraft?: RestoredDraft | null
  droppedFiles?: { key: number; files: Array<File> } | null
  /** A reply to a passage the user selected; it rides ahead of their next message. */
  addedReply?: { key: number; reply: PassageReply } | null
  models?: Array<ModelOption>
  modelSelectionEnabled?: boolean
  selection?: ModelSelection | null
  onSelectionChange?: (next: ModelSelection | null) => void
  /** Repos the user can target. When provided with onRepoChange, a repo picker is shown. */
  repos?: Array<{ full_name: string }>
  selectedRepo?: string | null
  /** While anything the target pickers show is still loading; they hold their place, hidden, rather than appearing or swapping labels in view. */
  targetPending?: boolean
  onRepoChange?: (repo: string | null) => void
  /** The signed-in person while they must link GitHub; the repo picker then asks them to. */
  gitHubLinkUser?: SessionUser
  /** Desktop-only execution target. Omit this prop to keep the control out of the web UI. */
  runTarget?: RunTarget
  onRunTargetChange?: (next: RunTarget) => void
  /** The thread is still moving to the run target, checkout and all. */
  runTargetPending?: boolean
  localRepos?: Array<DesktopProject>
  selectedLocalRepoPath?: string | null
  selectedLocalRepoBranch?: string | null
  localRepoBranches?: Array<DesktopProjectRef>
  localWorkspaceMode?: DesktopWorkspaceMode
  localWorktreeLabel?: string
  onLocalWorkspaceModeChange?: (next: DesktopWorkspaceMode) => void
  onSelectLocalRepo?: (cwd: string) => void
  onClearLocalRepo?: () => void
  onAddLocalRepo?: () => void
  onRemoveLocalRepo?: (cwd: string) => void
  onRefreshLocalRepoBranch?: () => void
  onSelectLocalRepoBranch?: (branch: string) => void
  /** Projects a new cloud thread can be filed in; the picker shows with `onProjectChange`. */
  projects?: Array<Project>
  selectedProjectId?: string | null
  onProjectChange?: (projectId: string | null) => void
  onCreateProject?: (name: string) => void
  /** Workspaces a new thread can boot from. The picker appears only when there are several. */
  workspaceOptions?: Array<WorkspaceOption>
  selectedWorkspace?: string | null
  onWorkspaceChange?: (slug: string | null) => void
  /** Paths offered by `@` autocomplete — in a thread, the files the agent has touched. */
  mentionPaths?: Array<string>
  skills?: Array<Skill>
  contextUsage?: ContextWindowMeterProps
  /** Route/model the Auto router picked for the current run. */
  routed?: { route?: string; modelId?: string | null } | null
}

function attachmentMimeType(file: File): string {
  return SUPPORTED_IMAGE_TYPES.has(file.type) ||
    SUPPORTED_FILE_TYPES.has(file.type)
    ? file.type
    : "application/octet-stream"
}

/** An attachment that goes to the sandbox rather than to the model as an image. */
function isFileChunk(chunk: ImageChunk): boolean {
  return !SUPPORTED_IMAGE_TYPES.has(chunk.mimeType)
}

function fileToImageChunk(file: File): Promise<ImageChunk | null> {
  if (file.size > MAX_IMAGE_BYTES) return Promise.resolve(null)
  const mimeType = attachmentMimeType(file)

  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : ""
      const base64 = dataUrl.split(",")[1]
      resolve(
        base64 ? { kind: "image", base64, mimeType, fileName: file.name } : null
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
  includeOffloadCommand = false,
  slackChannels: Array<SlackChannelOption> = [],
  recentSlackChannels: ReadonlyArray<string> = [],
  slackUsers: Array<SlackUserOption> = []
): Array<ComposerCommandItem> {
  const query = trigger.query.toLowerCase()

  if (trigger.kind === "slack-channel") {
    return rankSlackChannels(slackChannels, query, recentSlackChannels)
      .slice(0, MAX_MENTION_SUGGESTIONS)
      .map((channel) => ({
        id: `channel:${channel.id}`,
        type: "slack-channel" as const,
        channelId: channel.id,
        name: channel.name,
        label: `#${channel.name}`,
        description: [
          recentSlackChannels.includes(channel.id) ? "recently used" : null,
          channel.is_member ? null : "bot not in channel",
        ]
          .filter(Boolean)
          .join(" · "),
      }))
  }

  if (trigger.kind === "slash-command" || trigger.kind === "skill-command") {
    const search = new FuzzySearch(query)
    const skillItems = skills.map((skill) => ({
      id: `skill:${skill.name}`,
      type: "skill" as const,
      name: skill.name,
      label: `/${skill.name}`,
      description: skill.description,
    }))
    if (trigger.kind === "skill-command")
      return search.rank(
        skillItems,
        (item) => item.name,
        (item) => item.description
      )

    const skillNames = new Set(skills.map((skill) => skill.name))
    return search.rank(
      [
        ...SLASH_COMMANDS.filter(
          (spec) =>
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
      ],
      (item) => item.label,
      (item) => item.description
    )
  }

  const paths: Array<ComposerCommandItem> = mentionPaths
    .filter((path) => new FuzzySearch(query).matches(path))
    .map((path) => ({
      id: `path:${path}`,
      type: "path",
      path,
      label: path.slice(path.lastIndexOf("/") + 1),
      description: path,
    }))
  const people: Array<ComposerCommandItem> = slackUsers
    .filter((user) =>
      [user.name, user.display_name, user.real_name].some((name) =>
        new FuzzySearch(query).matches(name)
      )
    )
    .slice(
      0,
      paths.length > 0 ? MAX_MENTION_SUGGESTIONS / 2 : MAX_MENTION_SUGGESTIONS
    )
    .map((user) => ({
      id: `user:${user.id}`,
      type: "slack-user",
      userId: user.id,
      label: `@${user.display_name || user.name}`,
      description:
        user.real_name && user.real_name !== user.display_name
          ? `${user.real_name} · @${user.name}`
          : `@${user.name}`,
    }))
  return [...people, ...paths].slice(0, MAX_MENTION_SUGGESTIONS)
}

/** Prompt editor with autocomplete, model selection, attachments, and send/stop controls. */
export const ChatComposer = memo(function ChatComposer({
  draftKey,
  placeholder = "Ask Robin to build, fix bugs, explore",
  autoFocus = false,
  compact = false,
  disabled = false,
  busy = false,
  canOffload = false,
  activeRun,
  onStop,
  stoppable,
  onSubmit,
  followUpBehavior = "steer",
  restoreDraft = null,
  droppedFiles = null,
  addedReply = null,
  models = [],
  modelSelectionEnabled = true,
  selection = null,
  onSelectionChange,
  repos,
  selectedRepo = null,
  onRepoChange,
  gitHubLinkUser,
  runTarget,
  onRunTargetChange,
  runTargetPending,
  localRepos = [],
  selectedLocalRepoPath = null,
  selectedLocalRepoBranch = null,
  localRepoBranches = [],
  localWorkspaceMode = "local",
  localWorktreeLabel,
  onLocalWorkspaceModeChange,
  onSelectLocalRepo,
  onClearLocalRepo,
  onAddLocalRepo,
  onRemoveLocalRepo,
  onRefreshLocalRepoBranch,
  onSelectLocalRepoBranch,
  projects = [],
  selectedProjectId = null,
  targetPending = false,
  onProjectChange,
  onCreateProject,
  workspaceOptions = [],
  selectedWorkspace = null,
  onWorkspaceChange,
  mentionPaths = [],
  skills = [],
  contextUsage,
  routed,
}: ChatComposerProps) {
  const [value, setValue] = useState(
    () => composerDrafts.read(draftKey)?.text ?? ""
  )
  const [cursor, setCursor] = useState(value.length)
  const [pendingImages, setPendingImages] = useState<Array<ImageChunk>>(
    () => composerDrafts.read(draftKey)?.images ?? []
  )
  const [pendingReplies, setPendingReplies] = useState<Array<PendingReply>>(
    () => composerDrafts.read(draftKey)?.replies ?? []
  )
  useEffect(() => {
    composerDrafts.write(draftKey, value, pendingImages, pendingReplies)
  }, [draftKey, value, pendingImages, pendingReplies])
  const [appliedReply, setAppliedReply] = useState<typeof addedReply>(null)
  if (addedReply !== appliedReply) {
    setAppliedReply(addedReply)
    if (addedReply)
      setPendingReplies((prev) => [
        ...prev,
        { id: `added:${addedReply.key}`, reply: addedReply.reply },
      ])
  }
  const [draggingPath, setDraggingPath] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [activeItemId, setActiveItemId] = useState<string | null>(null)
  const [dismissedTriggerKey, setDismissedTriggerKey] = useState<string | null>(
    null
  )
  const [modelPickerOpen, setModelPickerOpen] = useState(false)
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
      ...(modelSelectionEnabled && models.length > 0
        ? [
            {
              id: "open-model-picker",
              label: "Choose model",
              aliases: ["model picker", "change model", "reasoning effort"],
              shortcuts: ["mod+shift+m"],
              group: "Composer",
              run: () => setModelPickerOpen(true),
            },
          ]
        : []),
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
    [activeRun?.running, modelSelectionEnabled, models.length]
  )
  useRegisterAppCommands(composerShortcuts)

  const editorRef = useRef<ComposerPromptEditorHandle | null>(null)

  const didAutoFocus = useRef(false)
  useEffect(() => {
    if (autoFocus && !didAutoFocus.current) {
      if (
        !document.activeElement?.closest(
          'input, textarea, select, [contenteditable]:not([contenteditable="false"]), dialog, [role="dialog"], [role="alertdialog"]'
        )
      )
        editorRef.current?.focus()
      didAutoFocus.current = true
    }
  }, [autoFocus])

  useEffect(() => {
    if (addedReply) editorRef.current?.focusAtEnd()
  }, [addedReply])

  // Synchronous double-submit guard: blocks a same-tick second send (Enter +
  // click, or two rapid Enters) before React re-renders. Scoped to the send
  // request only — never the run lifecycle.
  const submittingRef = useRef(false)

  const trigger = useMemo(
    () => detectComposerTrigger(value, cursor),
    [cursor, value]
  )
  const triggerKey = trigger ? `${trigger.kind}:${trigger.rangeStart}` : null
  const slackChannels = useSlackChannelDirectory(
    trigger?.kind === "slack-channel"
  ).data?.channels
  const slackUsers = useQuery({
    queryKey: ["slackUsers"],
    queryFn: api.listSlackUsers,
    enabled: trigger?.kind === "path",
    staleTime: 300_000,
  })
  const recentSlackChannels = useRecentSlackChannels()
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
            modelSelectionEnabled && models.length > 0,
            canOffload,
            slackChannels,
            recentSlackChannels,
            slackUsers.data?.users
          )
        : [],
    [
      mentionPaths,
      models.length,
      modelSelectionEnabled,
      skills,
      trigger,
      canOffload,
      slackChannels,
      recentSlackChannels,
      slackUsers.data,
    ]
  )
  const menuOpen =
    trigger !== null &&
    (commandItems.length > 0 || trigger.kind === "path") &&
    dismissedTriggerKey !== triggerKey
  const activeItem =
    commandItems.find((item) => item.id === activeItemId) ??
    commandItems[0] ??
    null

  const attachmentBase64 = { images: 0, files: 0 }
  for (const image of pendingImages) {
    attachmentBase64[isFileChunk(image) ? "files" : "images"] +=
      image.base64.length
  }
  const imageSizeError =
    attachmentBase64.images > MAX_IMAGE_BATCH_BASE64
      ? "Images must total 50MB or less. Remove an attachment to send."
      : null
  const fileSizeError =
    attachmentBase64.files > MAX_FILE_BATCH_BASE64
      ? "Files must total 100MB or less. Remove an attachment to send."
      : null
  const attachmentSizeError = imageSizeError ?? fileSizeError
  const imageCount = pendingImages.filter((i) => !isFileChunk(i)).length
  const filesWhileBusy = busy && pendingImages.some(isFileChunk)
  const selectedModelSupportsImages = useMemo(() => {
    if (!selection || imageCount === 0) return true
    return models.some((m) => m.id === selection.modelId && m.supports_images)
  }, [models, imageCount, selection])

  const composerEmpty =
    value.trim().length === 0 &&
    pendingImages.length === 0 &&
    pendingReplies.length === 0
  const readyToSubmit =
    !disabled &&
    !isSubmitting &&
    !attachmentSizeError &&
    selectedModelSupportsImages &&
    !filesWhileBusy &&
    !composerEmpty
  const run = useComposerRun({
    activeRun,
    canSubmit: readyToSubmit,
    onStop,
    runningLabel:
      followUpBehavior === "steer" ? "Steer agent" : "Queue message",
    stoppable,
    stopOnEscape: !menuOpen && !modelPickerOpen,
    submitting: isSubmitting,
  })
  const canSubmit = readyToSubmit && !run.stopping

  const applyPrompt = useCallback((nextValue: string, nextCursor: number) => {
    setValue(nextValue)
    setCursor(nextCursor)
    setDismissedTriggerKey(null)
    setActiveItemId(null)
  }, [])

  const restoredKeyRef = useRef<number | null>(null)
  // Kept with the draft, so a suggestion still replaces the last one after a remount.
  const lastSuggestionRef = useRef<Suggestion | null>(
    composerDrafts.suggestion(draftKey) ?? null
  )
  useEffect(() => {
    if (!restoreDraft || restoredKeyRef.current === restoreDraft.key) return
    restoredKeyRef.current = restoreDraft.key
    const current = editorRef.current?.readSnapshot()?.value ?? value
    const previous = lastSuggestionRef.current
    const untouched =
      restoreDraft.replacesSuggestion &&
      current.trim() === (previous?.text ?? "").trim()
    if (restoreDraft.replacesSuggestion) {
      lastSuggestionRef.current = {
        text: restoreDraft.text,
        images: restoreDraft.images,
      }
      composerDrafts.suggest(draftKey, lastSuggestionRef.current)
    }
    const next = [restoreDraft.text, untouched ? "" : current]
      .filter((part) => part.trim().length > 0)
      .join("\n\n")
    applyPrompt(next, next.length)
    // A replaced suggestion takes its images with it.
    const replaced = untouched ? (previous?.images ?? []) : []
    if (restoreDraft.images.length > 0 || replaced.length > 0) {
      // oxlint-disable-next-line react/set-state-in-effect
      setPendingImages((prev) => [
        ...restoreDraft.images,
        ...prev.filter((image) => !replaced.includes(image)),
      ])
    }
    const restoredReplies = restoreDraft.replies ?? []
    if (restoredReplies.length > 0) {
      setPendingReplies((prev) => [
        ...restoredReplies.map((reply, index) => ({
          id: `restored:${restoreDraft.key}:${index}`,
          reply,
        })),
        ...prev,
      ])
    }
    editorRef.current?.focusAtEnd()
  }, [applyPrompt, draftKey, restoreDraft, value])

  const handleSubmit = useCallback(
    async (options?: SubmitOptions) => {
      const submissionBlocked =
        submittingRef.current ||
        disabled ||
        run.stopping ||
        !!attachmentSizeError
      if (submissionBlocked) return
      // The editor is the source of truth for what is on screen; a keystroke that
      // has not yet round-tripped through state would otherwise be dropped.
      const snapshot = editorRef.current?.readSnapshot()
      const trimmed = (snapshot?.value ?? value).trim()
      const attached = pendingImages.length + pendingReplies.length
      if (trimmed.length === 0 && attached === 0) return

      if (trimmed === "/offload" && (!canOffload || attached)) {
        setComposerError(
          attached
            ? "Offloading does not accept attachments."
            : "Offloading requires an idle, existing conversation."
        )
        return
      }

      const submission = {
        text: trimmed,
        images: pendingImages,
        replies: pendingReplies.map((pending) => pending.reply),
      }
      submittingRef.current = true
      setIsSubmitting(true)
      applyPrompt("", 0)
      setPendingImages([])
      setPendingReplies([])
      setComposerError(null)
      try {
        await onSubmit?.(submission, options)
      } catch {
        // Caller surfaces send errors (e.g. via react-query mutation state).
      } finally {
        submittingRef.current = false
        setIsSubmitting(false)
      }
    },
    [
      applyPrompt,
      attachmentSizeError,
      disabled,
      run.stopping,
      onSubmit,
      pendingImages,
      pendingReplies,
      value,
      canOffload,
    ]
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

      if (
        item.type === "path" ||
        item.type === "skill" ||
        item.type === "slack-user" ||
        item.type === "slack-channel"
      ) {
        if (item.type === "slack-channel")
          rememberSlackChannels([item.channelId])
        const next = replaceTextRange(
          value,
          trigger.rangeStart,
          trigger.rangeEnd,
          item.type === "path"
            ? mentionReplacementText(item.path)
            : item.type === "slack-channel"
              ? `${slackChannelReference(item.channelId, item.name)} `
              : item.type === "slack-user"
                ? `<@${item.userId}> `
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
      if (menuOpen && key === "Escape") {
        setDismissedTriggerKey(triggerKey)
        return true
      }
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
            if (
              (trigger?.kind === "slash-command" ||
                trigger?.kind === "skill-command") &&
              activeItemId !== activeItem.id &&
              !activeItem.label
                .slice(1)
                .toLowerCase()
                .startsWith(trigger.query.toLowerCase())
            )
              break
            selectCommandItem(activeItem)
            return true
          case "Tab":
            selectCommandItem(activeItem)
            return true
        }
      }

      if (key === "Enter" && !event.shiftKey) {
        if (canSubmit) {
          void handleSubmit({ alternate: event.metaKey || event.ctrlKey })
        }
        // Swallow it either way: a bare Enter must never insert a newline in a
        // composer whose Enter means "send".
        return true
      }
      return false
    },
    [
      activeItem,
      activeItemId,
      trigger,
      canSubmit,
      commandItems,
      handleSubmit,
      menuOpen,
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
    const rejected = nextImages.length - validImages.length
    setComposerError(
      rejected
        ? `${rejected} attachment${rejected > 1 ? "s were" : " was"} skipped: attachments must be 10MB or smaller.`
        : null
    )
    if (validImages.length === 0) return
    setPendingImages((prev) => [...prev, ...validImages])
  }, [])

  useEffect(() => {
    if (droppedFiles)
      void Promise.resolve().then(() => addFiles(droppedFiles.files))
  }, [addFiles, droppedFiles])

  const handlePathDragOver = (event: React.DragEvent<HTMLFormElement>) => {
    if (disabled || !event.dataTransfer.types.includes(COMPOSER_PATH_DRAG_MIME))
      return
    event.preventDefault()
    setDraggingPath(true)
  }

  const handlePathDrop = (event: React.DragEvent<HTMLFormElement>) => {
    setDraggingPath(false)
    const path = event.dataTransfer.getData(COMPOSER_PATH_DRAG_MIME)
    if (disabled || !path) return
    event.preventDefault()
    const separator = value.length === 0 || /\s$/.test(value) ? "" : " "
    const nextValue = `${value}${separator}${mentionReplacementText(path)}`
    applyPrompt(nextValue, nextValue.length)
    editorRef.current?.focusAtEnd()
  }

  const drawerError = attachmentSizeError ?? composerError
  const drawerMessage =
    drawerError ??
    (!selectedModelSupportsImages
      ? `The selected model does not accept image input. Remove the image${imageCount > 1 ? "s" : ""} or switch to a vision-enabled model to send.`
      : filesWhileBusy
        ? "Files can be sent once the agent finishes its run."
        : null)

  const executionTarget = runTarget &&
    (runTarget === "local" ||
      (typeof window !== "undefined" && window.openSweDesktop)) && (
      <RunTargetSelector
        onChange={onRunTargetChange}
        pending={runTargetPending}
        value={runTarget}
      />
    )
  const targetControls = (onRepoChange || runTarget || onWorkspaceChange) && (
    <>
      {runTarget !== "local" && onWorkspaceChange && (
        <WorkspaceSelector
          workspaces={workspaceOptions}
          selectedSlug={selectedWorkspace}
          onChange={onWorkspaceChange}
          side="top"
        />
      )}
      {runTarget !== "local" && onProjectChange && onCreateProject && (
        <ProjectSelector
          onCreate={onCreateProject}
          onRepoChange={gitHubLinkUser ? undefined : onRepoChange}
          onSelect={onProjectChange}
          projects={projects}
          repos={gitHubLinkUser ? undefined : repos}
          selectedId={selectedProjectId}
          selectedRepo={selectedRepo}
        />
      )}
      {runTarget !== "local" && onRepoChange && gitHubLinkUser && (
        <GitHubLinkRepoSelector user={gitHubLinkUser} side="top" />
      )}
      {runTarget !== "local" &&
        onRepoChange &&
        !gitHubLinkUser &&
        !onProjectChange && (
          <RepoSelector
            emptySelectionLabel="Don't work in a repository"
            noMatchesLabel="No matching repositories"
            onRepoChange={onRepoChange}
            placeholder="Select repository"
            repos={repos}
            searchPlaceholder="Search repositories…"
            selectedRepo={selectedRepo}
            side="top"
          />
        )}
      {runTarget === "local" && (
        <LocalProjectSelector
          onAdd={onAddLocalRepo}
          onClear={onClearLocalRepo}
          onRemove={onRemoveLocalRepo}
          onSelect={onSelectLocalRepo}
          projects={localRepos}
          selectedPath={selectedLocalRepoPath}
          side="top"
        />
      )}
      {runTarget === "local" &&
        localRepoBranches.length > 0 &&
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
      {runTarget === "local" &&
        localRepoBranches.length > 0 &&
        onSelectLocalRepoBranch && (
          <LocalWorkspaceSelector
            onChange={onLocalWorkspaceModeChange}
            value={localWorkspaceMode}
            worktreeLabel={localWorktreeLabel}
          />
        )}
    </>
  )

  return (
    <div
      className={cn(
        "relative w-full font-sans text-xs",
        compact ? "max-w-none" : "max-w-2xl"
      )}
    >
      <MacawChatComposer
        {...run.composer}
        data-chat-composer
        aria-label="Message composer"
        className="relative z-floating-bar"
        disabled={disabled}
        submitDisabled={!canSubmit}
        onSubmit={(event) => {
          event.preventDefault()
          void handleSubmit()
        }}
        onFilesSelected={(files) => void addFiles(files)}
        onDragOver={handlePathDragOver}
        onDragLeave={() => setDraggingPath(false)}
        onDrop={handlePathDrop}
        drawerIntent={drawerError ? "error" : "warning"}
        drawer={
          drawerMessage && (
            <p role={drawerError ? "alert" : "status"} className="text-xs">
              {drawerMessage}
            </p>
          )
        }
      >
        {menuOpen && (
          <ComposerCommandMenu
            activeItemId={activeItem?.id ?? null}
            items={commandItems}
            onHighlight={setActiveItemId}
            onSelect={selectCommandItem}
            emptyStateText={
              trigger.kind === "path"
                ? slackUsers.isPending
                  ? "Loading people…"
                  : slackUsers.isError
                    ? "Could not load Slack people. Type @ again to retry."
                    : "No matching people or files."
                : undefined
            }
            triggerKind={trigger.kind}
          />
        )}

        {draggingPath && (
          <div className="pointer-events-none absolute inset-0 z-drag-overlay flex items-center justify-center rounded-xl bg-elevated/80 backdrop-blur-sm">
            <span className="rounded-md bg-brand-subtle px-space-3 py-space-1 text-xs font-medium text-brand-primary">
              Drop to mention this file
            </span>
          </div>
        )}

        <ComposerReplyCards
          disabled={disabled}
          replies={pendingReplies}
          onRemove={(id) =>
            setPendingReplies((prev) =>
              prev.filter((pending) => pending.id !== id)
            )
          }
        />

        {pendingImages.length > 0 && (
          <ChatComposerAttachments>
            {pendingImages.map((image, index) => (
              <ChatComposerAttachment
                key={`${image.fileName ?? "image"}-${index}`}
                name={image.fileName || (isFileChunk(image) ? "file" : "image")}
                contentType={image.mimeType}
                variant={isFileChunk(image) ? "file" : "thumbnail"}
                disabled={disabled}
                preview={
                  isFileChunk(image) ? undefined : (
                    <img
                      alt=""
                      src={`data:${image.mimeType};base64,${image.base64}`}
                    />
                  )
                }
                onRemove={() =>
                  setPendingImages((prev) => prev.filter((_, i) => i !== index))
                }
              />
            ))}
          </ChatComposerAttachments>
        )}

        <ComposerPromptEditor
          className={compact ? "min-h-[36px]" : "min-h-[52px]"}
          cursor={cursor}
          disabled={disabled}
          editorRef={editorRef}
          onChange={(nextValue, nextCursor) => {
            if (nextValue !== value || nextCursor !== cursor)
              setActiveItemId(null)
            setValue(nextValue)
            setCursor(nextCursor)
          }}
          onCommandKeyDown={handleCommandKeyDown}
          placeholder={
            busy && !disabled
              ? followUpBehavior === "steer"
                ? "Send a message to steer the run..."
                : "Send a message to queue next..."
              : placeholder
          }
          skillNames={skillNames}
          value={value}
        />

        <ChatComposerToolbar
          actions={
            <>
              <ContextWindowMeter {...contextUsage} />
              <ComposerPrimaryAction run={run} />
            </>
          }
        >
          <ChatComposerAttachmentMenu disabled={disabled} />
          <div className="flex min-w-0 flex-wrap items-center gap-space-1">
            {modelSelectionEnabled && models.length > 0 && (
              <ModelPicker
                disabled={disabled}
                models={models}
                onOpenChange={setModelPickerOpen}
                onSelectionChange={onSelectionChange}
                open={modelPickerOpen}
                requireImageSupport={imageCount > 0}
                routed={routed}
                selection={selection}
                triggerClassName="h-7 max-w-full rounded-md px-space-2 text-xs/relaxed text-tertiary hover:bg-surface-level-2 hover:text-primary"
              />
            )}
          </div>
        </ChatComposerToolbar>
      </MacawChatComposer>

      {targetControls && (
        <div className="mx-space-3 -mt-space-4 flex min-w-0 items-center gap-space-4 rounded-b-xl border border-t-0 border-subtle bg-surface-level-2 px-space-4 pt-space-5 pb-space-2 text-xs dark:border-transparent">
          <div className={cn("contents", targetPending && "invisible")}>
            {targetControls}
          </div>
          <div className="ml-auto shrink-0">{executionTarget}</div>
        </div>
      )}
    </div>
  )
})
