import type { ReactNode } from "react"
import { Link } from "@tanstack/react-router"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Text } from "@langchain/macaw-components/Text"
import { LogoMark } from "@/components/Branding"
import { FleetChat } from "@/features/agents/components/FleetChat"
import {
  ConnectLangSmithButton,
  useLangSmithConnection,
} from "@/features/settings/components/ConnectionsSection"
import { useProfile } from "@/lib/profile"

export function FleetHome({
  threadId,
  onThreadCreated,
}: {
  threadId?: string
  onThreadCreated: (threadId: string) => void
}) {
  const profile = useProfile()
  const langsmith = useLangSmithConnection()
  if (profile.isPending || langsmith.isPending) return null
  if (profile.data?.experimental_fleet !== true) {
    return (
      <FleetNotice
        title="Fleet is an experiment"
        body="Turn on Fleet chats in Experiments to chat with Fleet from Open SWE."
        action={
          <Button
            as={<Link to="/my-settings/experiments" />}
            size="md"
            className="w-full"
          >
            Open Experiments
          </Button>
        }
      />
    )
  }
  if (langsmith.data?.connected) {
    return <FleetChat threadId={threadId} onThreadCreated={onThreadCreated} />
  }
  if (langsmith.data?.available === false) {
    return (
      <FleetNotice
        title="Connect LangSmith to use Fleet"
        body="LangSmith sign-in isn't set up on this Open SWE deployment. Ask an admin to configure it."
      />
    )
  }
  return (
    <FleetNotice
      title="Connect LangSmith to use Fleet"
      body="Fleet chats run in your LangSmith workspace, as you. Connect once and your chats show up here and in LangSmith."
      action={<ConnectLangSmithButton size="md" className="w-full" />}
      footnote="You can disconnect any time in Settings → Connections."
    />
  )
}

function FleetNotice({
  title,
  body,
  action,
  footnote,
}: {
  title: string
  body: string
  action?: ReactNode
  footnote?: string
}) {
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
              {title}
            </Text>
            <Text variant="sm" color="secondary">
              {body}
            </Text>
          </div>
        </div>
        {action}
        {footnote && (
          <Text variant="xs" color="tertiary" className="text-center">
            {footnote}
          </Text>
        )}
      </Card>
    </div>
  )
}
