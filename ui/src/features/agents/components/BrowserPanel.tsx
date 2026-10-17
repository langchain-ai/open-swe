import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { Text } from "@langchain/macaw-components/Text"
import { GlobeIcon } from "@phosphor-icons/react/dist/ssr/Globe"

import {
  useAgentThreadBrowser,
  useSetThreadBrowserController,
} from "@/features/agents/lib/queries"

const SCOPE_LABELS = {
  user: "Your browser",
  workspace: "Workspace browser",
} as const

/**
 * The browser window a thread's agent opened, shown through LangSmith's own
 * page for it. Anyone who can prompt the thread can watch it, take it over to
 * click and type (to sign in, say), and hand it back to the agent.
 */
export function BrowserPanel({ threadId }: { threadId: string }) {
  const browser = useAgentThreadBrowser(threadId, true)
  const setController = useSetThreadBrowserController(threadId)

  if (browser.isPending) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    )
  }
  if (!browser.data) {
    return (
      <div className="flex flex-1 items-center justify-center p-space-5">
        <EmptyState
          size="sm"
          icon={GlobeIcon}
          title="No browser to show"
          description={
            browser.error?.message ??
            "The agent hasn't opened a browser in this thread."
          }
        />
      </div>
    )
  }

  const isHuman = browser.data.controller === "user"
  const tab = browser.data.tabs.at(-1)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-space-2 border-b border-default px-space-3 py-space-2">
        <Text
          variant="sm"
          color="secondary"
          className="min-w-0 flex-1 truncate"
        >
          {tab?.title || tab?.url || SCOPE_LABELS[browser.data.scope]}
        </Text>
        <Text variant="xs" color="tertiary" className="shrink-0">
          {SCOPE_LABELS[browser.data.scope]}
        </Text>
        <Button
          size="xs"
          color={isHuman ? "primary" : "secondary"}
          variant={isHuman ? "normal" : "outlined"}
          disabled={setController.isPending}
          onClick={() => setController.mutate(isHuman ? "agent" : "user")}
        >
          {isHuman ? "Hand back" : "Take control"}
        </Button>
      </div>
      {browser.data.reason && !isHuman ? (
        <div className="px-space-3 pt-space-2">
          <Banner intent="info" title="The agent needs you">
            {browser.data.reason}
          </Banner>
        </div>
      ) : null}
      {browser.data.act_url ? (
        <iframe
          title="Browser window"
          src={browser.data.act_url}
          allow="clipboard-read; clipboard-write"
          className="min-h-0 w-full flex-1 border-0 bg-surface-level-2"
        />
      ) : (
        <div className="flex flex-1 items-center justify-center p-space-5">
          <EmptyState
            size="sm"
            icon={GlobeIcon}
            title="No link to the window"
            description="LangSmith returned no act link for this browser window."
          />
        </div>
      )}
    </div>
  )
}
