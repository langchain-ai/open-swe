import { useEffect, useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Text } from "@langchain/macaw-components/Text"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"

import { connectService, type SlackRecovery } from "@/lib/api"

export function SlackRecoveryDialog({
  recovery,
  onCancel,
}: {
  recovery: SlackRecovery
  onCancel: () => void
}) {
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState(recovery.error)
  useEffect(() => {
    const url = new URL(window.location.href)
    url.searchParams.delete("slack_recovery")
    url.searchParams.delete("recovery_error")
    window.history.replaceState(window.history.state, "", url)
  }, [])
  const cancel = () => {
    void window.openSweDesktop?.cancelConnectService?.("github")
    onCancel()
  }
  const connect = async () => {
    setConnecting(true)
    setError(undefined)
    try {
      const pending = connectService(
        "github",
        "/my-settings/connections",
        recovery.recoveryToken
      )
      if (!pending) return
      const result = await pending
      if (result === true) {
        window.location.reload()
      } else {
        setError(
          typeof result === "object" && result.error
            ? result.error
            : "Connection was cancelled. Try again and choose the GitHub account you previously used with Robin."
        )
      }
    } catch {
      setError("Could not connect GitHub. Please try again.")
    }
    setConnecting(false)
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) cancel()
      }}
    >
      <DialogContent
        title="Recover your Robin account"
        description="Your Slack is connected to an older Robin account. To keep your old chats and settings, prove that account is yours by connecting the GitHub account you previously used with Robin."
        className="w-[min(30rem,calc(100vw-2rem))]"
      >
        <div className="flex flex-col gap-space-4">
          {error && (
            <Text role="alert" variant="sm" color="error">
              {error}
            </Text>
          )}
          <div className="flex flex-wrap justify-end gap-space-2">
            <Button
              color="secondary"
              variant="outlined"
              size="md"
              onClick={cancel}
            >
              Cancel
            </Button>
            <Button
              size="md"
              leftDecorator={GithubLogoIcon}
              loading={connecting}
              disabled={connecting}
              onClick={() => void connect()}
            >
              {connecting
                ? "Waiting for GitHub…"
                : "Connect previous GitHub account"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
