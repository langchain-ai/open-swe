"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";
import { ArrowRight } from "lucide-react";
import { LangGraphLogoSVG } from "../icons/langgraph";
import { useRouter } from "next/navigation";

function AuthStatusContent() {
  const router = useRouter();
  const [isAuth, setIsAuth] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<"github" | "gitlab">("github");
  const [authenticatedProvider, setAuthenticatedProvider] = useState<"github" | "gitlab" | null>(null);
  const [githubToken, setGithubToken] = useState<string | null>(null);
  const [hasGitHubAppInstalled, setHasGitHubAppInstalled] = useState(false);
  const [isCheckingAppInstallation, setIsCheckingAppInstallation] = useState(false);
  const [isGitLabAuth, setIsGitLabAuth] = useState(false);

  useEffect(() => {
    checkAuthStatus();
  }, []);

  useEffect(() => {
    // Check if we should redirect to chat
    if (authenticatedProvider === "gitlab" && isGitLabAuth) {
      console.log("redirecting to chat - gitlab authenticated");
      router.push("/chat");
    } else if (authenticatedProvider === "github" && githubToken) {
      console.log("redirecting to chat - github authenticated");
      router.push("/chat");
    }
  }, [authenticatedProvider, githubToken, isGitLabAuth, router]);

  const checkAuthStatus = async () => {
    try {
      const response = await fetch("/api/auth/status");
      const data = await response.json();
      setIsAuth(data.authenticated);
      setAuthenticatedProvider(data.provider);

      // Check for GitLab auth
      if (data.provider === "gitlab") {
        setIsGitLabAuth(data.authenticated);
      }

      // Check for GitHub auth
      if (data.provider === "github") {
        // Check if GitHub app is installed
        setIsCheckingAppInstallation(true);
        const installationsResponse = await fetch("/api/github/installations");
        if (installationsResponse.ok) {
          setHasGitHubAppInstalled(true);
          // Get token
          const tokenResponse = await fetch("/api/github/token");
          if (tokenResponse.ok) {
            const tokenData = await tokenResponse.json();
            setGithubToken(tokenData.token);
          }
        } else {
          setHasGitHubAppInstalled(false);
        }
        setIsCheckingAppInstallation(false);
      }
    } catch (error) {
      console.error("Error checking auth status:", error);
      setIsAuth(false);
      setAuthenticatedProvider(null);
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
    !showGetStarted &&
    authenticatedProvider === "github" &&
    !hasGitHubAppInstalled &&
    !isCheckingAppInstallation;
  const showLoading =
    !showGetStarted &&
    !showInstallApp &&
    authenticatedProvider === "github" &&
    !githubToken;

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
  // Don't wrap with GitHubAppProvider here - it causes unnecessary API calls
  // The provider will be used in the chat page where it's actually needed
  return <AuthStatusContent />;
}
