import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo } from "react";

import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { loginUrl } from "@/lib/api";
import {
  DEFAULT_AUTH_REDIRECT,
  consumeAuthRedirect,
  getRememberedAuthRedirect,
  rememberAuthRedirect,
} from "@/lib/auth-redirect";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

type LoginSearch = { redirect?: string };

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): LoginSearch => ({
    redirect: typeof search.redirect === "string" ? search.redirect : undefined,
  }),
  component: Login,
});

function Login() {
  const session = useSession();
  const search = Route.useSearch();
  const redirectParam = search.redirect;
  const intendedPath = useMemo(
    () =>
      redirectParam
        ? rememberAuthRedirect(redirectParam)
        : getRememberedAuthRedirect() ?? DEFAULT_AUTH_REDIRECT,
    [redirectParam]
  );
  const authenticatedRedirect = useMemo(
    () => (session.data ? consumeAuthRedirect(redirectParam) : null),
    [redirectParam, session.data]
  );

  if (session.isLoading) {
    return (
      <main className="flex min-h-svh items-center justify-center p-6">
        <Skeleton className="h-40 w-80" />
      </main>
    );
  }

  if (authenticatedRedirect) {
    return <ClientRedirect path={authenticatedRedirect} />;
  }

  return (
    <main className="flex min-h-svh items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Sign in to Jarvis</CardTitle>
          <CardDescription>
            Use your GitHub account. We'll configure your default model, reasoning effort, and
            default repo for Slack/Linear/GitHub triggered runs.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <a
            href={loginUrl(intendedPath)}
            className={cn(buttonVariants({ size: "lg" }), "w-full")}
          >
            Continue with GitHub
          </a>
        </CardContent>
      </Card>
    </main>
  );
}

function ClientRedirect({ path }: { path: string }) {
  useEffect(() => {
    if (typeof window !== "undefined") window.location.replace(path);
  }, [path]);

  return (
    <main className="flex min-h-svh items-center justify-center p-6">
      <Skeleton className="h-40 w-80" />
    </main>
  );
}
