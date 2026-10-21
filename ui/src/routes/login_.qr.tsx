import { useMutation, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useEffect, useState } from "react"

import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Text } from "@langchain/macaw-components/Text"

import { LogoMark } from "@/components/Branding"
import { api } from "@/lib/api"
import { DEFAULT_AUTH_REDIRECT } from "@/lib/auth-redirect"
import { useIsHydrated } from "@/lib/hydration"
import { pageTitle } from "@/lib/pageTitle"
import { sessionQueryOptions } from "@/lib/session"

export const Route = createFileRoute("/login_/qr")({
  head: () => ({ meta: [{ title: pageTitle("Sign in") }] }),
  component: QrLogin,
})

function readFragmentCode(): string | null {
  if (typeof window === "undefined") return null
  return new URLSearchParams(window.location.hash.slice(1)).get("code")
}

function QrLogin() {
  const qc = useQueryClient()
  const hydrated = useIsHydrated()
  const [fragmentCode] = useState(readFragmentCode)
  const code = hydrated ? fragmentCode : null
  useEffect(() => {
    window.history.replaceState(null, "", window.location.pathname)
  }, [])
  const redeem = useMutation({
    meta: { silent: true },
    mutationFn: (value: string) => api.redeemQrLogin(value),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: sessionQueryOptions.queryKey })
      window.location.replace(DEFAULT_AUTH_REDIRECT)
    },
  })

  return (
    <main className="flex min-h-svh items-center justify-center bg-surface-level-1 p-space-5">
      <Card
        intent="plain"
        className="flex w-full max-w-md flex-col gap-space-5 p-space-6 shadow-md"
      >
        <div className="flex flex-col items-center gap-space-3 text-center">
          <LogoMark alt="Open SWE" className="size-8" />
          <Text as="h1" variant="h3">
            Sign in on this device
          </Text>
          <Text variant="sm" color="secondary">
            {redeem.isError
              ? `${redeem.error.message}. Show a new code on your computer and scan it again.`
              : code || !hydrated
                ? "Continue to sign in as the account that showed this QR code."
                : "This link has no sign-in code. Scan the QR code from Settings → General on your computer."}
          </Text>
        </div>
        <Button
          size="md"
          className="w-full"
          disabled={!code || redeem.isPending || redeem.isError}
          onClick={() => code && redeem.mutate(code)}
        >
          {redeem.isPending ? "Signing in…" : "Continue"}
        </Button>
      </Card>
    </main>
  )
}
