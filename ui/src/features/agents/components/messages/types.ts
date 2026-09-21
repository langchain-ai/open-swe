import type {
  Message,
  LocalRepo,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"

export interface ApprovalCallbacks {
  onApprove?: (approvalRequestId: string) => void
  onReject?: (approvalRequestId: string) => void
  onAutoApprove?: (approvalRequestId: string) => void
  /** Reveal a path in the side panel's diff view. */
  onOpenFile?: (filePath: string) => void
}

export interface LoadEarlier {
  loading: boolean
  onLoadEarlier: () => void
}

export type MessagesScrollControl = {
  scrollToBottom: () => void
}

export interface MessagesProps extends ApprovalCallbacks {
  messages: Array<Message>
  /** Cloud threads only; enables the git-sourced changed-files card per turn. */
  threadId?: string
  /** Identity for remembering the scroll position across navigation. */
  scrollKey?: string
  showPlanArtifact?: boolean
  emptyState?: React.ReactNode
  footer?: React.ReactNode
  pollWorkflowApprovalsWhileActive?: boolean
  queuedMessages?: Array<QueuedThreadMessage>
  /** Send a queued message now instead of waiting for its boundary. */
  onSteerQueuedMessage?: (id: string) => void
  /** Drop a queued message and hand it back to the composer. */
  onRemoveQueuedMessage?: (id: string) => void
  isStreaming: boolean
  /** Live run signal from `useStream().isLoading` — drives Streamdown token animation. */
  streamIsLoading?: boolean
  /** When set, drives the thinking spinner (stream + pending). Falls back to streamIsLoading/isStreaming. */
  isThinking?: boolean
  settingUpSandbox?: boolean
  isOffloading?: boolean
  /** Takes over the activity line while the event stream is reconnecting. */
  reconnectLabel?: string | null
  localRepo?: LocalRepo | null
  contentWidthClass?: string
  /** Horizontal padding on centered content (scroll track stays edge-to-edge). */
  contentPaddingClass?: string
  /** Extra scroll padding so content can scroll under a bottom overlay (e.g. floating prompt). */
  bottomInset?: number
  /** When "external", parent renders the scroll button (e.g. above a floating prompt). */
  scrollButtonSlot?: "internal" | "external"
  /** Set when turns older than the loaded window remain on the server. */
  loadEarlier?: LoadEarlier | null
  onShowScrollToBottomChange?: (show: boolean) => void
  scrollControlRef?: React.MutableRefObject<MessagesScrollControl | null>
}
