import { Dialog } from "@base-ui/react/dialog"
import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { IoLogoSlack } from "react-icons/io5"

import type { ModelOption } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api, slackConnectUrl } from "@/lib/api"
import {
  buildProfileUpdate,
  useOptions,
  useProfile,
  useSaveProfile,
} from "@/lib/profile"
import { useSession } from "@/lib/session"

/**
 * First-run onboarding modal: pick a default agent model, then connect Slack.
 *
 * The model step shows until the user has saved a default model; the Slack step
 * shows (where Sign in with Slack is enabled) until their Slack account is
 * linked. Both steps live in the same dialog so a new user is walked through
 * picking a model and connecting Slack in one place. Dismissing hides it for
 * the session; an incomplete step reappears on the next login.
 */
export function OnboardingDialog() {
  const session = useSession()
  const mapping = useQuery({ queryKey: ["myMapping"], queryFn: api.myMapping })
  const profile = useProfile()
  const options = useOptions()
  const save = useSaveProfile()
  const [dismissed, setDismissed] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const firstModel: ModelOption | undefined = options.data?.models[0]
  const defaultModel = options.data?.default_agent_model ?? firstModel?.id ?? ""
  const defaultEffort =
    options.data?.default_agent_reasoning_effort ??
    firstModel?.default_effort ??
    ""
  const [modelId, setModelId] = useState("")
  const [effort, setEffort] = useState("")

  useEffect(() => {
    if (!modelId && defaultModel) setModelId(defaultModel)
  }, [modelId, defaultModel])

  const currentModel: ModelOption | undefined =
    options.data?.models.find((m) => m.id === modelId) ?? firstModel

  useEffect(() => {
    if (!currentModel) return
    if (!effort || !currentModel.efforts.includes(effort)) {
      setEffort(currentModel.default_effort)
    }
  }, [currentModel, effort])

  const slackEnabled = session.data?.slack_oauth_enabled ?? false
  const slackConnected = !!mapping.data?.slack_user_id
  const hasDefaultModel = !!profile.data?.default_model

  const needsModel =
    !profile.isLoading && profile.data !== undefined && !hasDefaultModel
  const needsSlack =
    slackEnabled && !slackConnected && !mapping.isLoading && !mapping.isError
  const open = !dismissed && (needsModel || needsSlack)
  const step: "model" | "slack" = needsModel ? "model" : "slack"

  const handleSaveModel = () => {
    if (!modelId) return
    setError(null)
    save
      .mutateAsync(
        buildProfileUpdate(
          profile.data,
          { default_model: modelId, reasoning_effort: effort },
          defaultModel,
          defaultEffort
        )
      )
      .catch((e: Error) => setError(e.message))
  }

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
          {step === "model" ? (
            <div className="flex flex-col gap-4">
              <Dialog.Title className="text-sm font-medium">
                Choose your default model
              </Dialog.Title>
              <Dialog.Description className="text-xs text-muted-foreground">
                Pick the model Jarvis uses when you don't specify one. You can
                change this anytime in your settings.
              </Dialog.Description>
              <div className="flex flex-col gap-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-medium">Default model</span>
                  <Select
                    value={modelId}
                    onValueChange={(v) => v && setModelId(v)}
                  >
                    <SelectTrigger className="w-48">
                      <SelectValue placeholder="Pick a model" />
                    </SelectTrigger>
                    <SelectContent>
                      {options.data?.models.map((m) => (
                        <SelectItem key={m.id} value={m.id}>
                          {m.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-medium">Reasoning effort</span>
                  <Select
                    value={effort}
                    onValueChange={(v) => v && setEffort(v)}
                  >
                    <SelectTrigger className="w-48">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {currentModel?.efforts.map((e) => (
                        <SelectItem key={e} value={e}>
                          {e}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              {error && <p className="text-xs text-destructive">{error}</p>}
              <div className="mt-2 flex justify-end gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setDismissed(true)}
                >
                  Maybe later
                </Button>
                <Button
                  size="sm"
                  onClick={handleSaveModel}
                  disabled={!modelId || save.isPending}
                >
                  {save.isPending
                    ? "Saving…"
                    : needsSlack
                      ? "Save & continue"
                      : "Save"}
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-3">
                <IoLogoSlack className="size-6 shrink-0 text-muted-foreground" />
                <Dialog.Title className="text-sm font-medium">
                  Connect your Slack account
                </Dialog.Title>
              </div>
              <Dialog.Description className="text-xs text-muted-foreground">
                Connect Slack so that when you tag Jarvis, it can resolve your
                GitHub account. We use the email Slack verifies, which also lets
                Linear mentions resolve to you.
              </Dialog.Description>
              <div className="mt-2 flex justify-end gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setDismissed(true)}
                >
                  Maybe later
                </Button>
                <Button
                  size="sm"
                  onClick={() => window.location.assign(slackConnectUrl())}
                >
                  <IoLogoSlack className="size-4" />
                  Connect Slack
                </Button>
              </div>
            </div>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
