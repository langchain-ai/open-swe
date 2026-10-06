import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ArrowUpRight, Check, Loader2 } from "lucide-react"
import { SiNotion } from "react-icons/si"
import { toast } from "sonner"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { api, connectService } from "@/lib/api"

export function ServiceConnectionCard() {
  const credentials = useQuery({
    queryKey: ["myNotion"],
    queryFn: api.getMyNotionStatus,
    refetchOnWindowFocus: "always",
  })
  const { refetch } = credentials
  useEffect(() => {
    const refresh = () => void refetch()
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [refetch])
  const [connecting, setConnecting] = useState(false)
  const [cancelled, setCancelled] = useState(false)
  const connected = credentials.data?.connected === true

  const connect = async () => {
    setConnecting(true)
    setCancelled(false)
    try {
      const pending = connectService("notion", window.location.href, "_blank")
      if (pending) {
        const completed = await pending
        setCancelled(!completed)
        await credentials.refetch()
      }
    } catch {
      toast.error("Couldn't connect Notion. Please try again.")
    }
    setConnecting(false)
  }

  return (
    <section
      aria-label="Notion connection"
      className="my-2 max-w-lg rounded-control border border-line bg-panel p-4 text-body"
    >
      <div className="flex items-start gap-3">
        <div className="rounded-compact border border-line bg-canvas p-2.5">
          <SiNotion aria-hidden className="size-5" />
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-center gap-2 font-medium">
            Notion
            {connected && !credentials.isError && (
              <span className="inline-flex items-center gap-1 text-label text-primary">
                <Check aria-hidden className="size-3" /> Connected to your
                account
              </span>
            )}
          </div>
          <p className="text-ink-subtle">
            {connected
              ? "Continue this conversation in a private thread you own to use your Notion connection."
              : "Continue to Notion to choose what Open SWE can access, then return to this chat."}
          </p>
        </div>
      </div>
      <p className="mt-3 text-meta leading-relaxed text-ink-subtle">
        For your account only, in private threads you own. Connecting does not
        give other participants or shared channels access. Consent opens in a
        new browser tab; return here afterward. It does not resume the task
        automatically.
      </p>
      {cancelled && (
        <p role="status" className="mt-3 text-meta text-ink-subtle">
          Connection wasn't completed. You can try again.
        </p>
      )}
      <div className="mt-4 flex items-center justify-end gap-3">
        {credentials.isError ? (
          <>
            <p role="alert" className="text-label text-risk">
              Couldn't check your connection.
            </p>
            <Button
              size="compact"
              variant="outline"
              onClick={() => void credentials.refetch()}
            >
              Retry
            </Button>
          </>
        ) : (
          <Button
            size="compact"
            variant={connected ? "outline" : "default"}
            disabled={connecting || credentials.isPending}
            onClick={() => void connect()}
          >
            {connecting || credentials.isPending ? (
              <Loader2 aria-hidden className="size-3.5 animate-spin" />
            ) : (
              <ArrowUpRight aria-hidden className="size-3.5" />
            )}
            {connecting
              ? "Connecting…"
              : credentials.isPending
                ? "Checking connection…"
                : connected
                  ? "Reconnect Notion"
                  : "Connect Notion"}
          </Button>
        )}
      </div>
    </section>
  )
}
