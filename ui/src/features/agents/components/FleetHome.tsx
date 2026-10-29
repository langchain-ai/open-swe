import { FleetChat } from "@/features/agents/components/FleetChat"
import {
  ConnectLangSmithButton,
  useLangSmithConnection,
} from "@/features/settings/components/ConnectionsSection"

export function FleetHome({
  threadId,
  onThreadCreated,
}: {
  threadId?: string
  onThreadCreated: (threadId: string) => void
}) {
  const langsmith = useLangSmithConnection()
  if (langsmith.isPending) return null
  if (langsmith.data?.connected) {
    return <FleetChat threadId={threadId} onThreadCreated={onThreadCreated} />
  }
  const available = langsmith.data?.available !== false
  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center gap-space-3 p-space-6 text-center">
      <h1 className="text-xl font-semibold text-primary">
        Connect LangSmith to use Fleet
      </h1>
      <p className="text-sm text-secondary">
        {available
          ? "Fleet chats run in your LangSmith workspace, as you."
          : "LangSmith sign-in isn't configured on this Open SWE deployment."}
      </p>
      {available && <ConnectLangSmithButton size="sm" />}
    </div>
  )
}
