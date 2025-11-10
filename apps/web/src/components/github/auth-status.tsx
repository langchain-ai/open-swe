"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";
import { ArrowRight } from "lucide-react";
import { LangGraphLogoSVG } from "../icons/langgraph";
import { useGitHubToken } from "@/hooks/useGitHubToken";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { GitHubAppProvider } from "@/providers/GitHubApp";
import { useRouter } from "next/navigation";
import { useGitLabAuth } from "@/hooks/useGitLabAuth";

function AuthStatusContent() {
  const router = useRouter();
  const [isAuth, setIsAuth] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<"github" | "gitlab">("github");

  const {
    token: githubToken,
    fetchToken: fetchGitHubToken,
    isLoading: isTokenLoading,
  } = useGitHubToken();

  const {
    isInstalled: hasGitHubAppInstalled,
    isLoading: isCheckingAppInstallation,
  } = useGitHubAppProvider();

  const { isAuthenticated: isGitLabAuth, loading: isGitLabLoading } = useGitLabAuth();

  useEffect(() => {
    checkAuthStatus();
  }, []);

  useEffect(() => {
    if (isAuth && hasGitHubAppInstalled && !githubToken && !isTokenLoading) {
      // Fetch token when app is installed but we don't have a token yet
      fetchGitHubToken();
    }
  }, [
    isAuth,
    hasGitHubAppInstalled,
    githubToken,
    isTokenLoading,
    fetchGitHubToken,
  ]);

  useEffect(() => {
    if (githubToken || isGitLabAuth) {
      console.log("redirecting to chat");
      router.push("/chat");
    }
  }, [githubToken, isGitLabAuth]);

  const checkAuthStatus = async () => {
    try {
      const response = await fetch("/api/auth/status");
      const data = await response.json();
      setIsAuth(data.authenticated);
    } catch (error) {
      console.error("Error checking auth status:", error);
      setIsAuth(false);
    }
  };

  const handleLogin = () => {
    setIsLoading(true);
    if (selectedProvider === "github") {
      window.location.href = "/api/auth/github/login";
    } else {
      window.location.href = "/api/auth/gitlab/login";
    }
  };

  const handleInstallGitHubApp = () => {
    setIsLoading(true);
    window.location.href = "/api/github/installation";
  };

  const showGetStarted = !isAuth && !isGitLabAuth;
  const showInstallApp =
    !showGetStarted && selectedProvider === "github" && !hasGitHubAppInstalled && !isTokenLoading;
  const showLoading = !showGetStarted && !showInstallApp && !githubToken && !isGitLabAuth;

  useEffect(() => {
    if (!showGetStarted && !showInstallApp && !showLoading) {
      router.push("/chat");
    }
  }, [showGetStarted, showInstallApp, showLoading, router]);

  if (showGetStarted) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 flex w-full max-w-3xl flex-col rounded-lg border shadow-lg">
          <div className="flex flex-col gap-4 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                Get started
              </h1>
            </div>
            <p className="text-muted-foreground">
              Connect your {selectedProvider === "github" ? "GitHub" : "GitLab"} account to get started with Open SWE.
            </p>

            {/* Provider Selector */}
            <div className="flex gap-2 rounded-md border p-1">
              <button
                onClick={() => setSelectedProvider("github")}
                className={`flex-1 rounded px-3 py-2 text-sm font-medium transition-colors ${
                  selectedProvider === "github"
                    ? "bg-primary text-primary-foreground"
                    : "hover:bg-muted"
                }`}
              >
                <div className="flex items-center justify-center gap-2">
                  <GitHubSVG width="16" height="16" />
                  GitHub
                </div>
              </button>
              <button
                onClick={() => setSelectedProvider("gitlab")}
                className={`flex-1 rounded px-3 py-2 text-sm font-medium transition-colors ${
                  selectedProvider === "gitlab"
                    ? "bg-primary text-primary-foreground"
                    : "hover:bg-muted"
                }`}
              >
                <div className="flex items-center justify-center gap-2">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M23.955 13.587l-1.342-4.135-2.664-8.189a.455.455 0 00-.867 0L16.418 9.45H7.582L4.919 1.263a.455.455 0 00-.867 0L1.388 9.452.046 13.587a.924.924 0 00.331 1.031l11.625 8.445 11.625-8.445a.92.92 0 00.328-1.031z"/>
                  </svg>
                  GitLab
                </div>
              </button>
            </div>

            <Button
              onClick={handleLogin}
              disabled={isLoading}
            >
              {selectedProvider === "github" ? (
                <GitHubSVG width="16" height="16" />
              ) : (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M23.955 13.587l-1.342-4.135-2.664-8.189a.455.455 0 00-.867 0L16.418 9.45H7.582L4.919 1.263a.455.455 0 00-.867 0L1.388 9.452.046 13.587a.924.924 0 00.331 1.031l11.625 8.445 11.625-8.445a.92.92 0 00.328-1.031z"/>
                </svg>
              )}
              {isLoading ? "Connecting..." : `Connect ${selectedProvider === "github" ? "GitHub" : "GitLab"}`}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (showInstallApp) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 flex w-full max-w-3xl flex-col rounded-lg border shadow-lg">
          <div className="flex flex-col gap-4 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                One more step
              </h1>
            </div>
            <div className="text-muted-foreground flex items-center gap-2 text-sm">
              <span className="rounded-full bg-green-100 px-2 py-1 text-xs font-medium text-green-800">
                1. GitHub Login ✓
              </span>
              <ArrowRight className="h-3 w-3" />
              <span className="rounded-full bg-blue-100 px-2 py-1 text-xs font-medium text-blue-800">
                2. Repository Access
              </span>
            </div>
            <p className="text-muted-foreground">
              Great! Now we need access to your GitHub repositories. Install our
              GitHub App to grant access to specific repositories.
            </p>
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              <p>
                You'll be redirected to GitHub where you can select which
                repositories to grant access to.
              </p>
            </div>
            <Button
              onClick={handleInstallGitHubApp}
              disabled={isLoading || isCheckingAppInstallation}
              className="bg-black hover:bg-gray-800 dark:bg-white dark:hover:bg-gray-200"
            >
              <GitHubSVG
                width="16"
                height="16"
              />
              {isLoading || isCheckingAppInstallation
                ? "Loading..."
                : "Install GitHub App"}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (showLoading) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 flex w-full max-w-3xl flex-col rounded-lg border shadow-lg">
          <div className="flex flex-col gap-4 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                Loading...
              </h1>
            </div>
            <p className="text-muted-foreground">
              Setting up your GitHub integration...
            </p>
          </div>
        </div>
      </div>
    );
  }

  return null;
}

export default function AuthStatus() {
  return (
    <GitHubAppProvider>
      <AuthStatusContent />
    </GitHubAppProvider>
  );
}
