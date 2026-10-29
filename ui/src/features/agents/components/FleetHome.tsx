import { Card } from "@langchain/macaw-components/Card"
import { Text } from "@langchain/macaw-components/Text"
import { LogoMark } from "@/components/Branding"
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
    <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-space-5">
      <Card
        intent="plain"
        className="flex w-full max-w-md flex-col gap-space-5 p-space-6 shadow-md"
      >
        <div className="flex flex-col items-center gap-space-3 text-center">
          <LogoMark alt="" className="size-8" />
          <div className="flex flex-col gap-space-1">
            <Text as="h1" variant="h3">
              Connect LangSmith to use Fleet
            </Text>
            <Text variant="sm" color="secondary">
              {available
                ? "Fleet chats run in your LangSmith workspace, as you. Connect once and your chats show up here and in LangSmith."
                : "LangSmith sign-in isn't set up on this Open SWE deployment. Ask an admin to configure it."}
            </Text>
          </div>
        </div>
        {available && (
          <>
            <ConnectLangSmithButton size="md" className="w-full" />
            <Text variant="xs" color="tertiary" className="text-center">
              You can disconnect any time in Settings → Connections.
            </Text>
          </>
        )}
      </Card>
    </div>
  )
}
