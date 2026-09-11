import { Dialog } from "@base-ui/react/dialog"
import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { IoLogoSlack } from "react-icons/io5"

import { Button } from "@/components/ui/button"
import { api, connectService } from "@/lib/api"
import { useProfile } from "@/lib/profile"
import { useSession } from "@/lib/session"

const SLACK_ONBOARDING_DISMISSED_KEY = "open-swe.slack-onboarding-dismissed"

/**
 * First-run onboarding modal: connect Slack.
 *
 * New users get adaptive model routing out of the box, so there is no model
 * step — a default model is only prompted for if routing gets turned off in
 * settings. The Slack prompt shows (where Sign in with Slack is enabled) until
 * their Slack account is linked, and can be permanently dismissed in the
 * current browser.
 */
export function OnboardingDialog() {
  const session = useSession()
  const mapping = useQuery({ queryKey: ["myMapping"], queryFn: api.myMapping })
  useProfile()
  const [dismissed, setDismissed] = useState(false)
  const [slackDismissed, setSlackDismissed] = useState(false)

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setSlackDismissed(
      window.localStorage.getItem(SLACK_ONBOARDING_DISMISSED_KEY) === "true"
    )
  }, [])

  const slackEnabled = session.data?.slack_oauth_enabled ?? false
  const slackConnected = !!mapping.data?.slack_user_id

  const needsSlack =
    slackEnabled &&
    !slackConnected &&
    !slackDismissed &&
    !mapping.isLoading &&
    !mapping.isError
  const open = !dismissed && needsSlack

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) setDismissed(true)
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-popover p-6 text-popover-foreground shadow-md ring-1 ring-foreground/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-3">
              <IoLogoSlack className="size-6 shrink-0 text-muted-foreground" />
              <Dialog.Title className="text-sm font-medium">
                Connect your Slack account
              </Dialog.Title>
            </div>
            <Dialog.Description className="text-xs text-muted-foreground">
              Connect Slack so that when you tag Open SWE, it can resolve your
              GitHub account. We use the email Slack verifies, which also lets
              Linear mentions resolve to you.
            </Dialog.Description>
            <div className="mt-2 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  window.localStorage.setItem(
                    SLACK_ONBOARDING_DISMISSED_KEY,
                    "true"
                  )
                  setSlackDismissed(true)
                }}
              >
                Don't ask again
              </Button>
              <Button
                size="sm"
                onClick={() =>
                  void connectService("slack")?.finally(
                    () => void mapping.refetch()
                  )
                }
              >
                <IoLogoSlack className="size-4" />
                Connect Slack
              </Button>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
