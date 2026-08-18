import type {
  Message,
  Project,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"

export interface ApprovalCallbacks {
  onApprove?: (approvalRequestId: string) => void
  onReject?: (approvalRequestId: string) => void
  onAutoApprove?: (approvalRequestId: string) => void
  /** Reveal a path in the side panel's diff view. */
  onOpenFile?: (filePath: string) => void
}

export type MessagesScrollControl = {
  scrollToBottom: () => void
}

export interface MessagesProps extends ApprovalCallbacks {
  messages: Array<Message>
  /** Cloud threads only; enables the git-sourced changed-files card per turn. */
  threadId?: string
  queuedMessages?: Array<QueuedThreadMessage>
  isStreaming: boolean
  /** Live run signal from `useStream().isLoading` — drives Streamdown token animation. */
  streamIsLoading?: boolean
  /** When set, drives the thinking spinner (stream + pending). Falls back to streamIsLoading/isStreaming. */
  isThinking?: boolean
  settingUpSandbox?: boolean
  project?: Project | null
  contentWidthClass?: string
  /** Horizontal padding on centered content (scroll track stays edge-to-edge). */
  contentPaddingClass?: string
  /** Extra scroll padding so content can scroll under a bottom overlay (e.g. floating prompt). */
  bottomInset?: number
  /** When "external", parent renders the scroll button (e.g. above a floating prompt). */
  scrollButtonSlot?: "internal" | "external"
  onShowScrollToBottomChange?: (show: boolean) => void
  scrollControlRef?: React.MutableRefObject<MessagesScrollControl | null>
}
