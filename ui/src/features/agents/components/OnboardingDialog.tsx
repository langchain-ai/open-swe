import { Dialog } from "@base-ui/react/dialog"
import { IoLogoSlack } from "react-icons/io5"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { connectService } from "@/lib/api"
import { useDismissSlackOnboarding, useProfile } from "@/lib/profile"
import { useSession } from "@/lib/session"

/** Prompt unlinked users to connect Slack. */
export function OnboardingDialog() {
  const session = useSession()
  const profile = useProfile()
  const dismiss = useDismissSlackOnboarding()

  const slackEnabled = session.data?.slack_oauth_enabled ?? false
  const slackConnected = !!session.data?.slack_user_id
  const needsSlack =
    slackEnabled &&
    !slackConnected &&
    profile.isSuccess &&
    !profile.data.slack_onboarding_dismissed &&
    !session.isLoading &&
    !session.isError
  return (
    <Dialog.Root
      open={needsSlack}
      onOpenChange={(next) => {
        if (!next) dismiss.mutate()
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-compact bg-panel p-6 text-ink shadow-popup ring-1 ring-ink/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-3">
              <IoLogoSlack className="size-6 shrink-0 text-ink-subtle" />
              <Dialog.Title className="text-body font-medium">
                Connect your Slack account
              </Dialog.Title>
            </div>
            <Dialog.Description className="text-meta text-ink-subtle">
              Connect Slack so that when you tag Open SWE, it can resolve your
              GitHub account. We use the email Slack verifies, which also lets
              Linear mentions resolve to you.
            </Dialog.Description>
            <div className="mt-2 flex justify-end gap-2">
              <Button
                variant="outline"
                size="compact"
                onClick={() => dismiss.mutate()}
              >
                Don't ask again
              </Button>
              <Button
                size="compact"
                onClick={() =>
                  void connectService("slack")?.finally(
                    () => void session.refetch()
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
