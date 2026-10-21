import { useMutation, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useRouterState } from "@tanstack/react-router"

import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Text } from "@langchain/macaw-components/Text"

import { LogoMark } from "@/components/Branding"
import { ApiError, api } from "@/lib/api"
import { DEFAULT_AUTH_REDIRECT } from "@/lib/auth-redirect"
import { useIsHydrated } from "@/lib/hydration"
import { pageTitle } from "@/lib/pageTitle"
import { sessionQueryOptions } from "@/lib/session"

export const Route = createFileRoute("/login_/qr")({
  head: () => ({ meta: [{ title: pageTitle("QR sign-in") }] }),
  component: QrLogin,
})

function QrLogin() {
  const hash = useRouterState({ select: (state) => state.location.hash })
  const ready = useIsHydrated()
  const code = new URLSearchParams(hash.replace(/^#/, "")).get("code")
  return <QrLoginCard key={hash} code={code} ready={ready} />
}

function QrLoginCard({ code, ready }: { code: string | null; ready: boolean }) {
  const qc = useQueryClient()
  const redeem = useMutation({
    meta: { silent: true },
    mutationFn: (value: string) => api.redeemQrLogin(value),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: sessionQueryOptions.queryKey })
      window.location.replace(DEFAULT_AUTH_REDIRECT)
    },
  })
  const malformed = !code || !/^[A-Za-z0-9_-]{43}$/.test(code)
  const rejected =
    redeem.error instanceof ApiError &&
    (redeem.error.status === 400 || redeem.error.status === 422)
  const invalid = ready && (malformed || rejected)

  return (
    <main className="flex min-h-svh items-center justify-center bg-surface-level-1 p-space-5">
      <Card
        intent="plain"
        className="flex w-full max-w-sm flex-col gap-space-5 p-space-6 shadow-md"
      >
        <div className="flex flex-col items-center gap-space-3 text-center">
          <LogoMark alt="Open SWE" className="size-8" />
          <Text as="h1" variant="h2">
            {invalid ? "Invalid QR code" : "Sign in with QR code"}
          </Text>
          <Text
            variant="sm"
            color="secondary"
            role={invalid ? "alert" : undefined}
          >
            {!ready
              ? "Reading your sign-in code…"
              : invalid
                ? malformed
                  ? "This sign-in link is missing a valid QR code."
                  : "This QR code has expired or has already been used."
                : "Continue only if you scanned this code from your own computer. You'll sign in with the same account."}
          </Text>
          {invalid && (
            <Text variant="sm" color="secondary">
              On your computer, open Settings → General → Mobile and scan a new
              QR code.
            </Text>
          )}
          {redeem.isError && !invalid && (
            <Text variant="sm" color="error" role="alert">
              Couldn't sign in: {redeem.error.message}
            </Text>
          )}
        </div>
        {!invalid && (
          <Button
            size="md"
            className="w-full"
            disabled={!ready || malformed || redeem.isPending}
            onClick={() => code && redeem.mutate(code)}
          >
            {redeem.isPending
              ? "Signing in…"
              : redeem.isError
                ? "Try again"
                : "Continue"}
          </Button>
        )}
      </Card>
    </main>
  )
}
