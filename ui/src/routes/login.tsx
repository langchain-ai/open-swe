import { createFileRoute } from "@tanstack/react-router"
import { useEffect, useMemo } from "react"

import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Logo } from "@langchain/macaw-components/Logo"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Text } from "@langchain/macaw-components/Text"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"

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
      <main className="flex min-h-svh items-center justify-center bg-surface-level-1 p-space-5">
        <Skeleton className="h-40 w-80" />
      </main>
    )
  }

  if (authenticatedRedirect) {
    return <ClientRedirect path={authenticatedRedirect} />
  }

  return (
    <main className="flex min-h-svh items-center justify-center bg-surface-level-1 p-space-5">
      <Card
        intent="plain"
        className="flex w-full max-w-md flex-col gap-space-5 p-space-6 shadow-md"
      >
        <div className="flex flex-col items-center gap-space-3 text-center">
          <Logo brand="langchain" variant="logomark" size="lg" />
          <div className="flex flex-col gap-space-1">
            <Text as="h1" variant="h3">
              Sign in to Open SWE
            </Text>
            <Text variant="sm" color="secondary">
              Use your GitHub account. We'll configure your default model,
              reasoning effort, and default repo for Slack/Linear/GitHub
              triggered runs.
            </Text>
          </div>
        </div>
        <Button
          as={<a href={loginUrl(intendedPath)} />}
          size="md"
          leftDecorator={GithubLogoIcon}
          className="w-full"
        >
          Continue with GitHub
        </Button>
      </Card>
    </main>
  )
}

function ClientRedirect({ path }: { path: string }) {
  useEffect(() => {
    if (typeof window !== "undefined") window.location.replace(path)
  }, [path])

  return (
    <main className="flex min-h-svh items-center justify-center bg-surface-level-1 p-space-5">
      <Skeleton className="h-40 w-80" />
    </main>
  )
}
