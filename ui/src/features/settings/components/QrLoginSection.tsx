import { useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import { encode } from "uqr"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Text } from "@langchain/macaw-components/Text"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { api } from "@/lib/api"

const REFRESH_LEAD_MS = 30_000

function QrCode({ value }: { value: string }) {
  const { path, size } = useMemo(() => {
    const qr = encode(value, { border: 4 })
    return {
      size: qr.size,
      path: qr.data
        .flatMap((row, y) =>
          row.map((dark, x) => (dark ? `M${x} ${y}h1v1h-1z` : ""))
        )
        .join(""),
    }
  }, [value])
  return (
    <svg
      role="img"
      aria-label="QR code to sign in on your phone"
      viewBox={`0 0 ${size} ${size}`}
      shapeRendering="crispEdges"
      className="size-64 rounded-md bg-white"
    >
      <path d={path} fill="black" />
    </svg>
  )
}

/** Mounted only while the dialog is open, so every opening mints a fresh code. */
function QrLoginCode() {
  const qr = useQuery({
    queryKey: ["qrLogin"],
    queryFn: api.issueQrLogin,
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
  })
  const expiresAt = qr.data?.expires_at
  const { refetch } = qr
  useEffect(() => {
    if (!expiresAt) return
    const delay = Date.parse(expiresAt) - Date.now() - REFRESH_LEAD_MS
    const timer = window.setTimeout(() => void refetch(), Math.max(delay, 0))
    return () => window.clearTimeout(timer)
  }, [expiresAt, refetch])

  return (
    <div
      className="flex flex-col items-center gap-space-3"
      data-dd-privacy="hidden"
    >
      {qr.data && !qr.isError ? (
        <QrCode value={qr.data.url} />
      ) : (
        <Skeleton className="size-64" />
      )}
      <Text variant="sm" color="secondary">
        {qr.isError
          ? `Couldn't create a code: ${qr.error.message}`
          : "Open your camera and point it at the code."}
      </Text>
    </div>
  )
}

export function QrLoginSection() {
  const [open, setOpen] = useState(false)

  return (
    <SettingsSection title="Mobile">
      <SettingsRow
        label="Sign in on your phone"
        description="Scan a QR code with your phone's camera to sign in there without GitHub."
        control={
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            onClick={() => setOpen(true)}
          >
            Show QR code
          </Button>
        }
      />
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          title="Sign in on your phone"
          description="Anyone who scans this code signs in as you. It works once and expires in two minutes."
        >
          {open && <QrLoginCode />}
        </DialogContent>
      </Dialog>
    </SettingsSection>
  )
}
