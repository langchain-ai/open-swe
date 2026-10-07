import { createFileRoute } from "@tanstack/react-router"
import { useEffect, useMemo } from "react"
import type { ReactNode } from "react"

import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { buttonVariants } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { GitHub } from "@/components/glyphs"
import { OpenSweMarkTile } from "@/components/rail/OpenSweMark"
import { loginUrl } from "@/lib/api"
import { pageTitle } from "@/lib/pageTitle"
import {
  DEFAULT_AUTH_REDIRECT,
  consumeAuthRedirect,
  getRememberedAuthRedirect,
  rememberAuthRedirect,
} from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

type LoginSearch = { redirect?: string }

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): LoginSearch => ({
    redirect: typeof search.redirect === "string" ? search.redirect : undefined,
  }),
  head: () => ({ meta: [{ title: pageTitle("Sign in") }] }),
  component: Login,
})

/** The sign-in door: one card centred on the desk, no rail. */
function LoginFrame({ children }: { children: ReactNode }) {
  return (
    <Stack
      render={<main />}
      align="center"
      justify="center"
      padding="lg"
      className="min-h-svh bg-desk"
    >
      <Stack
        gap="xl"
        bg="canvas"
        border="line"
        radius="shell"
        className="w-full max-w-md px-6 py-8"
      >
        <Inline gap="sm" align="center">
          <OpenSweMarkTile />
          <Box render={<span />} className="text-title font-semibold text-ink">
            Open SWE
          </Box>
        </Inline>
        {children}
      </Stack>
    </Stack>
  )
}

function Login() {
  const session = useSession()
  const search = Route.useSearch()
  const redirectParam = search.redirect
  const intendedPath = useMemo(
    () =>
      redirectParam
        ? rememberAuthRedirect(redirectParam)
        : (getRememberedAuthRedirect() ?? DEFAULT_AUTH_REDIRECT),
    [redirectParam]
  )
  const authenticatedRedirect = useMemo(
    () => (session.data ? consumeAuthRedirect(redirectParam) : null),
    [redirectParam, session.data]
  )
  if (session.isLoading) {
    return (
      <LoginFrame>
        <Skeleton className="h-control w-full" />
      </LoginFrame>
    )
  }

  if (authenticatedRedirect) {
    return <ClientRedirect path={authenticatedRedirect} />
  }

  return (
    <LoginFrame>
      <Stack gap="xs">
        <Box
          render={<h1 />}
          className="text-page font-semibold tracking-tightish text-ink"
        >
          Sign in to Open SWE
        </Box>
        <Box render={<p />} className="text-body text-ink-subtle">
          Use your GitHub account. We'll configure your default model, reasoning
          effort, and default repo for Slack/Linear/GitHub triggered runs.
        </Box>
      </Stack>
      <a
        href={loginUrl(intendedPath)}
        className={cn(buttonVariants({ variant: "primary" }), "w-full")}
      >
        <Icon icon={GitHub} size="md" />
        Continue with GitHub
      </a>
    </LoginFrame>
  )
}

function ClientRedirect({ path }: { path: string }) {
  useEffect(() => {
    if (typeof window !== "undefined") window.location.replace(path)
  }, [path])

  return (
    <LoginFrame>
      <Skeleton className="h-control w-full" />
    </LoginFrame>
  )
}
