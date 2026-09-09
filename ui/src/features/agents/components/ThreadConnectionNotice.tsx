import { useAgentThreadRuntime } from "../lib/AgentThreadStreamProvider"

export function ThreadConnectionNotice() {
  const stream = useAgentThreadRuntime()
  if (stream.connection !== "recovering") return null
  return (
    <div
      role="status"
      className="mx-auto flex w-full max-w-3xl items-center gap-2 px-4 py-2 text-sm text-muted-foreground"
    >
      <span>Connection interrupted. Reconnecting…</span>
      <button
        type="button"
        className="underline"
        onClick={() => void stream.refresh()}
      >
        Retry now
      </button>
    </div>
  )
}
