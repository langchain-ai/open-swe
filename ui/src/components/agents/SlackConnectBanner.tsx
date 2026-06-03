import { useQuery } from "@tanstack/react-query"
import { IoLogoSlack } from "react-icons/io5"

import { Button } from "@/components/ui/button"
import { api, slackConnectUrl } from "@/lib/api"
import { useSession } from "@/lib/session"

/**
 * Onboarding nudge shown until the user links Slack. A first-time user (no
 * Slack mapping yet) sees it immediately on the landing page so they're guided
 * to connect right after signing in; it disappears once connected.
 */
export function SlackConnectBanner() {
  const session = useSession()
  const mapping = useQuery({ queryKey: ["myMapping"], queryFn: api.myMapping })

  const slackEnabled = session.data?.slack_oauth_enabled ?? false
  const connected = !!mapping.data?.slack_user_id
  if (!slackEnabled || mapping.isLoading || mapping.isError || connected)
    return null

  return (
    <div className="mx-auto mb-6 flex w-full max-w-[640px] items-center justify-between gap-4 rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        <IoLogoSlack className="size-5 shrink-0 text-[var(--ui-text-dim)]" />
        <div className="min-w-0">
          <div className="text-sm font-medium text-[var(--ui-text)]">
            Connect your Slack account
          </div>
          <div className="text-xs text-[var(--ui-text-dim)]">
            So Open SWE can act as you when you tag it from Slack.
          </div>
        </div>
      </div>
      <Button
        size="sm"
        onClick={() => window.location.assign(slackConnectUrl())}
      >
        <IoLogoSlack className="size-4" />
        Connect Slack
      </Button>
    </div>
  )
}
