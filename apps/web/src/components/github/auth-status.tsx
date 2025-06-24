"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";
import { ArrowRight } from "lucide-react";
import { LangGraphLogoSVG } from "../icons/langgraph";
import { useGitHubToken } from "@/hooks/useGitHubToken";

export default function AuthStatus() {
  const [isAuth, setIsAuth] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const [hasGitHubAppInstalled, setHasGitHubAppInstalled] = useState<
    boolean | null
  >(() => {
    if (typeof window === "undefined") return null;
    const cached = localStorage.getItem("github_app_installed");
    return cached === "true" ? true : cached === "false" ? false : null;
  });

  const [isCheckingAppInstallation, setIsCheckingAppInstallation] =
    useState(false);
  const {
    token: githubToken,
    fetchToken: fetchGitHubToken,
    isLoading: isTokenLoading,
  } = useGitHubToken();

  useEffect(() => {
    checkAuthStatus();
  }, []);

  useEffect(() => {
    if (isAuth) {
      const cachedInstallationStatus = localStorage.getItem(
        "github_app_installed",
      );
      if (cachedInstallationStatus === "true") {
        setHasGitHubAppInstalled(true);
        // Fetch token if we don't have one yet
        if (!githubToken && !isTokenLoading) {
          fetchGitHubToken();
        }
      } else if (cachedInstallationStatus === "false") {
        setHasGitHubAppInstalled(false);
      } else {
        checkGitHubAppInstallation();
      }
    }
  }, [isAuth, githubToken]);

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

  const checkGitHubAppInstallation = async () => {
    setIsCheckingAppInstallation(true);
    try {
      const response = await fetch("/api/github/repositories");
      if (response.ok) {
        setHasGitHubAppInstalled(true);
        localStorage.setItem("github_app_installed", "true");

        await fetchGitHubToken();
      } else {
        const errorData = await response.json();
        if (errorData.error.includes("installation")) {
          setHasGitHubAppInstalled(false);
          localStorage.setItem("github_app_installed", "false");
        } else {
          setHasGitHubAppInstalled(false);
          localStorage.setItem("github_app_installed", "false");
        }
      }
    } catch (error) {
      console.error("Error checking GitHub App installation:", error);
      setHasGitHubAppInstalled(false);
      localStorage.setItem("github_app_installed", "false");
    } finally {
      setIsCheckingAppInstallation(false);
    }
  };

  const handleLogin = () => {
    setIsLoading(true);
    window.location.href = "/api/auth/github/login";
  };

  const handleInstallGitHubApp = () => {
    setIsLoading(true);

    localStorage.removeItem("github_app_installed");
    window.location.href = "/api/github/installation";
  };

  if (!isAuth) {
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
              Connect your GitHub account to get started with Open SWE.
            </p>
            <Button
              onClick={handleLogin}
              disabled={isLoading}
            >
              <GitHubSVG
                width="16"
                height="16"
              />
              {isLoading ? "Connecting..." : "Connect GitHub"}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // Step 2: GitHub App Installation (only show if authenticated but app not installed)
  // Only show modal if we've definitively determined the app is not installed
  // Don't show while we're still loading or have cached positive status
  if (isAuth && hasGitHubAppInstalled === false && !isTokenLoading) {
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
              className="bg-black hover:bg-gray-800"
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

  // Only render StreamSession when we have a valid token
  if (!githubToken) {
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
}
