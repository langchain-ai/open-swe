import {
  MicrophoneIcon,
  MicrophoneSlashIcon,
  PhoneDisconnectIcon,
} from "@phosphor-icons/react"
import { useEffect, useMemo, useSyncExternalStore } from "react"

import { Button, IconButton } from "@/components/ui/button"
import {
  Popover,
  PopoverDescription,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { VoiceManager } from "@/features/agents/lib/voice"
import { cn } from "@/lib/utils"

export function VoicePanel({
  navigate,
}: {
  navigate: (threadId: string) => void
}) {
  const manager = useMemo(() => new VoiceManager(navigate), [navigate])
  const voice = useSyncExternalStore(
    manager.subscribe,
    manager.getSnapshot,
    manager.getSnapshot
  )

  useEffect(() => () => manager.destroy(), [manager])

  const active = voice.status !== "idle" && voice.status !== "error"
  const status = {
    idle: "Ready",
    requesting: "Requesting microphone…",
    connecting: "Connecting…",
    connected: voice.muted ? "Connected · muted" : "Connected · listening",
    stopping: "Stopping…",
    error: "Could not connect",
  }[voice.status]

  return (
    <Popover>
      <PopoverTrigger
        aria-label="Open voice manager"
        title="Voice manager"
        className={cn(
          "relative flex size-6 items-center justify-center rounded-full border border-border bg-background transition-colors hover:bg-accent",
          active && "border-primary/50"
        )}
      >
        <span
          className={cn(
            "size-2.5 rounded-full bg-muted-foreground/50",
            active && "animate-pulse bg-primary",
            voice.status === "error" && "bg-destructive"
          )}
        />
      </PopoverTrigger>
      <PopoverPopup align="start" side="bottom" className="w-80 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <PopoverTitle>Voice manager</PopoverTitle>
            <PopoverDescription className="mt-1">
              You are speaking with an AI-generated voice. Audio is sent to
              OpenAI.
            </PopoverDescription>
          </div>
          <span
            className={cn(
              "mt-0.5 size-2 shrink-0 rounded-full bg-muted-foreground/50",
              active && "animate-pulse bg-primary",
              voice.status === "error" && "bg-destructive"
            )}
          />
        </div>

        <div className="mt-4 flex items-center justify-between gap-3">
          <div aria-live="polite" className="text-xs font-medium">
            {status}
          </div>
          <div className="flex gap-1.5">
            {active ? (
              <>
                <IconButton
                  variant="outline"
                  aria-label={
                    voice.muted ? "Unmute microphone" : "Mute microphone"
                  }
                  title={voice.muted ? "Unmute" : "Mute"}
                  disabled={voice.status !== "connected"}
                  onClick={() => manager.toggleMute()}
                >
                  {voice.muted ? <MicrophoneSlashIcon /> : <MicrophoneIcon />}
                </IconButton>
                <Button
                  variant="destructive"
                  disabled={voice.status === "stopping"}
                  onClick={() => manager.stop()}
                >
                  <PhoneDisconnectIcon />
                  Stop
                </Button>
              </>
            ) : (
              <Button onClick={() => void manager.start()}>
                <MicrophoneIcon />
                Start
              </Button>
            )}
          </div>
        </div>

        {voice.error && (
          <p role="alert" className="mt-3 text-xs text-destructive">
            {voice.error}
          </p>
        )}

        <div className="mt-4 border-t border-border pt-3">
          <div className="mb-2 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
            Transcript
          </div>
          <div
            aria-live="polite"
            className="max-h-52 space-y-2 overflow-y-auto text-xs leading-relaxed"
          >
            {voice.transcript.length ? (
              voice.transcript.map((entry, index) => (
                <p key={`${entry.role}-${index}`}>
                  <strong>{entry.role}:</strong> {entry.text}
                </p>
              ))
            ) : (
              <p className="text-muted-foreground">
                Conversation text will appear here.
              </p>
            )}
          </div>
        </div>
      </PopoverPopup>
    </Popover>
  )
}
