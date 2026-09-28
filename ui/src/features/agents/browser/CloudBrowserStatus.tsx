import { useEffect, useRef } from "react"
import { LoaderCircle } from "lucide-react"

import type { CloudBrowserConnectionState } from "@/features/agents/browser/browserTabStore"
import { Button } from "@/components/ui/button"

interface Props {
  connection: CloudBrowserConnectionState
  onInstall: () => void
  onRetry: () => void
}

/**
 * Overlay for everything between "panel opened" and "frames arriving" on a
 * cloud tab: connecting, a sandbox with no Chromium, the install log, and
 * failures with a retry.
 */
export function CloudBrowserStatus({ connection, onInstall, onRetry }: Props) {
  const logRef = useRef<HTMLPreElement | null>(null)
  useEffect(() => {
    const node = logRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [connection.installOutput.length])

  if (connection.status === "connecting") {
    return (
      <div className="pointer-events-none absolute inset-x-0 top-0 z-30 flex justify-center pt-3">
        <span className="flex items-center gap-2 rounded-full border border-border/70 bg-popover/95 px-3 py-1 text-xs text-muted-foreground shadow-md backdrop-blur">
          <LoaderCircle className="size-3.5 animate-spin" />
          Connecting to the sandbox browser…
        </span>
      </div>
    )
  }

  if (connection.status === "browser-missing") {
    return (
      <StatusShell
        title="No browser in this sandbox"
        description="The sandbox has no Chromium yet. Install one now, or bake it into the workspace's setup script so every sandbox starts with it."
      >
        {connection.installCommand ? (
          <pre className="max-w-full overflow-x-auto rounded-md border border-border/70 bg-muted/40 px-3 py-2 text-left text-xs">
            {connection.installCommand}
          </pre>
        ) : null}
        <Button type="button" size="sm" onClick={onInstall}>
          Install Chromium
        </Button>
      </StatusShell>
    )
  }

  if (connection.status === "installing") {
    return (
      <StatusShell
        title="Installing Chromium"
        description="This downloads a few hundred megabytes into the sandbox and can take a couple of minutes."
      >
        <pre
          ref={logRef}
          className="h-40 w-full max-w-lg overflow-auto rounded-md border border-border/70 bg-muted/40 px-3 py-2 text-left text-[0.7rem] leading-relaxed text-muted-foreground"
        >
          {connection.installOutput.length > 0
            ? connection.installOutput.join("\n")
            : "Starting…"}
        </pre>
      </StatusShell>
    )
  }

  if (connection.status === "error" || connection.status === "closed") {
    return (
      <StatusShell
        title={
          connection.status === "error" ? "Browser unavailable" : "Disconnected"
        }
        description={
          connection.error ??
          (connection.status === "error"
            ? "The sandbox browser could not be reached."
            : "The connection to the sandbox browser closed.")
        }
      >
        <Button type="button" size="sm" onClick={onRetry}>
          Reconnect
        </Button>
      </StatusShell>
    )
  }

  return null
}

function StatusShell(props: {
  title: string
  description: string
  children?: React.ReactNode
}) {
  return (
    <div className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-3 bg-background px-8 text-center">
      <h3 className="text-sm font-medium text-foreground">{props.title}</h3>
      <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">
        {props.description}
      </p>
      {props.children}
    </div>
  )
}
