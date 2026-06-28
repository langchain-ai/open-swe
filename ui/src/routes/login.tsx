import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import type { FormEvent } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, loginUrl } from "@/lib/api";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/login")({ component: Login });

function Login() {
  const session = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [mode, setMode] = useState<"login" | "reset-request" | "reset-confirm">("login");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetToken, setResetToken] = useState(() => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("reset_token") ?? "";
  });

  const currentMode = resetToken ? "reset-confirm" : mode;

  if (session.isLoading) {
    return (
      <main className="flex min-h-svh items-center justify-center p-6">
        <Skeleton className="h-40 w-80" />
      </main>
    );
  }

  if (session.data) {
    return <Navigate to="/my-settings" />;
  }

  async function submitPasswordLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      await api.passwordLogin({ email, password });
      window.location.assign("/my-settings");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Sign in failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitResetRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      await api.requestPasswordReset({ email });
      setMessage("Reset request accepted.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Reset request failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitResetConfirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      await api.confirmPasswordReset({ token: resetToken, password: resetPassword });
      setResetToken("");
      setMode("login");
      setMessage("Password updated.");
      if (typeof window !== "undefined") {
        window.history.replaceState({}, "", "/login");
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Password reset failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-svh items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Sign in to open-swe</CardTitle>
          <CardDescription>
            Use your workspace account or continue with GitHub.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {currentMode === "login" ? (
            <form className="space-y-3" onSubmit={submitPasswordLogin}>
              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
              </div>
              {error ? <p className="text-xs text-destructive">{error}</p> : null}
              {message ? <p className="text-xs text-muted-foreground">{message}</p> : null}
              <Button type="submit" size="lg" className="w-full" disabled={submitting}>
                {submitting ? "Signing in..." : "Sign in"}
              </Button>
              <Button
                type="button"
                variant="link"
                size="sm"
                className="h-auto px-0"
                onClick={() => {
                  setMode("reset-request");
                  setError(null);
                  setMessage(null);
                }}
              >
                Reset password
              </Button>
            </form>
          ) : currentMode === "reset-request" ? (
            <form className="space-y-3" onSubmit={submitResetRequest}>
              <div className="space-y-1.5">
                <Label htmlFor="reset-email">Email</Label>
                <Input
                  id="reset-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </div>
              {error ? <p className="text-xs text-destructive">{error}</p> : null}
              {message ? <p className="text-xs text-muted-foreground">{message}</p> : null}
              <Button type="submit" size="lg" className="w-full" disabled={submitting}>
                {submitting ? "Submitting..." : "Request reset"}
              </Button>
              <Button
                type="button"
                variant="link"
                size="sm"
                className="h-auto px-0"
                onClick={() => {
                  setMode("login");
                  setError(null);
                  setMessage(null);
                }}
              >
                Back to sign in
              </Button>
            </form>
          ) : (
            <form className="space-y-3" onSubmit={submitResetConfirm}>
              <div className="space-y-1.5">
                <Label htmlFor="new-password">New password</Label>
                <Input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  value={resetPassword}
                  onChange={(event) => setResetPassword(event.target.value)}
                  required
                />
              </div>
              {error ? <p className="text-xs text-destructive">{error}</p> : null}
              <Button type="submit" size="lg" className="w-full" disabled={submitting}>
                {submitting ? "Updating..." : "Update password"}
              </Button>
            </form>
          )}
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <div className="h-px flex-1 bg-border" />
            <span>or</span>
            <div className="h-px flex-1 bg-border" />
          </div>
          <a
            href={loginUrl()}
            className={cn(buttonVariants({ size: "lg", variant: "outline" }), "w-full")}
          >
            Continue with GitHub
          </a>
        </CardContent>
      </Card>
    </main>
  );
}
