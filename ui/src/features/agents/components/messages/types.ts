import type {
  Message,
  Project,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"

export interface MessagesProps {
  /** Reveal a path in the side panel's diff view. */
  onOpenFile?: (filePath: string) => void
  messages: Array<Message>
  /** Cloud threads only; enables the git-sourced changed-files card per turn. */
  threadId?: string
  showPlanArtifact?: boolean
  queuedMessages?: Array<QueuedThreadMessage>
  isStreaming: boolean
  /** Live run signal from `useStream().isLoading` — drives Streamdown token animation. */
  streamIsLoading?: boolean
  /** When set, drives the thinking spinner (stream + pending). Falls back to streamIsLoading/isStreaming. */
  isThinking?: boolean
  settingUpSandbox?: boolean
  project?: Project | null
  contentWidthClass?: string
}
