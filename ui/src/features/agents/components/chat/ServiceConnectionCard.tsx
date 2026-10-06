import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { ProviderMark } from "@langchain/gtm-platform-design-system/patterns/provider-mark"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Frame,
  FrameDescription,
  FramePanel,
} from "@langchain/gtm-platform-design-system/ui/frame"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import { ArrowUpRight, Check } from "@/components/glyphs"
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
  const busy = connecting || credentials.isPending

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
    <Frame
      role="region"
      aria-label="Notion connection"
      className="w-full max-w-lg"
    >
      <Inline gap="md" align="start" className="px-3 pt-2.5 pb-2">
        <ProviderMark provider="notion" />
        <Stack gap="xs" className="min-w-0 flex-1">
          <Inline gap="sm" align="center" wrap>
            <Box render={<span />} className="text-label font-semibold text-ink">
              Notion
            </Box>
            {connected && !credentials.isError && (
              <Badge tier="quiet" tone="positive">
                <Icon icon={Check} size="sm" />
                Connected to your account
              </Badge>
            )}
          </Inline>
          <FrameDescription>
            {connected
              ? "Continue this conversation in a private thread you own to use your Notion connection."
              : "Continue to Notion to choose what Open SWE can access, then return to this chat."}
          </FrameDescription>
        </Stack>
      </Inline>
      <FramePanel>
        <FrameDescription>
          For your account only, in private threads you own. Connecting does
          not give other participants or shared channels access. Consent opens
          in a new browser tab; return here afterward. It does not resume the
          task automatically.
        </FrameDescription>
        {cancelled && (
          <Box render={<p role="status" />} className="text-meta text-ink">
            Connection wasn't completed. You can try again.
          </Box>
        )}
      </FramePanel>
      <Inline gap="sm" align="center" justify="end" className="px-1.5 pt-1 pb-1.5">
        {credentials.isError ? (
          <>
            <Box render={<p role="alert" />} className="text-label text-risk">
              Couldn't check your connection.
            </Box>
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
            variant={connected ? "outline" : "primary"}
            disabled={busy}
            onClick={() => void connect()}
          >
            {busy ? (
              <Spinner size="sm" />
            ) : (
              <Icon icon={ArrowUpRight} size="sm" />
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
      </Inline>
    </Frame>
  )
}
