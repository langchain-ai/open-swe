import type {
  AgentStream,
  StreamConnection,
} from "@/features/agents/lib/stream/streamPool"
import type { SubagentToolCall } from "@/features/agents/lib/transcript/reducer"
import type { ImageChunk, Message } from "@/features/agents/lib/types"

/** The human message and run configuration a new run starts from. */
export interface ThreadRunInput {
  /** Omitted for a message-less run such as `/offload`. */
  message?: {
    /** Client-minted id, shared by the graph's message and the transcript row. */
    id: string
    text: string
    images?: ReadonlyArray<ImageChunk>
  }
  configurable: Record<string, unknown>
}

interface ThreadSourceShared {
  threadId: string
  /** The transcript, as rows the message renderers understand. */
  messages: Array<Message>
  /** A run is live as this client sees it. */
  isRunning: boolean
  /** The one-time transcript load has not produced anything yet. */
  isHydrating: boolean
  /** Settles with that load; a rejection means the transcript is unavailable. */
  hydration: Promise<unknown>
  error: unknown
  isOffloading: boolean
  routed: { route?: string; modelId?: string | null } | null
  connection: StreamConnection
  /** Context tokens the last model call reported, when the source knows them. */
  contextTokens: number | null
  /**
   * Start a run. A rejection means the run could not be started; resolution
   * says nothing about the run finishing (the SDK stream resolves only when it
   * does, the transcript log as soon as the command is accepted).
   */
  startRun: (input: ThreadRunInput) => Promise<void>
  /** Cancel the live run. Resolves once the attempt settled, either way. */
  stop: () => Promise<void>
}

/** The thread is served by the SDK's `useStream`. */
export interface StreamThreadSource extends ThreadSourceShared {
  kind: "stream"
  stream: AgentStream
}

/** The thread is served by the append-only transcript event log. */
export interface TranscriptThreadSource extends ThreadSourceShared {
  kind: "transcript"
  /** Nested tool calls under a subagent namespace, for the subagent card. */
  subagentToolCalls: (
    namespace: ReadonlyArray<string>
  ) => Array<SubagentToolCall>
}

export type ThreadSource = StreamThreadSource | TranscriptThreadSource
