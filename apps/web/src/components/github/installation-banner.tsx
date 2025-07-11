"use client";

import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { InstallationPrompt } from "./installation-prompt";
import { useState, useEffect } from "react";
import { cn } from "@/lib/utils";

const GITHUB_INSTALLATION_SEEN_KEY = "github_installation_seen";

export function GitHubInstallationBanner() {
  const { isInstalled, isLoading } = useGitHubAppProvider();
  const [dismissed, setDismissed] = useState(false);
  const [isNewUser, setIsNewUser] = useState(false);

  useEffect(() => {
    // Check if this might be a new user (no installation history in localStorage)
    const hasSeenInstallation = localStorage.getItem(
      GITHUB_INSTALLATION_SEEN_KEY,
    );
    if (!hasSeenInstallation && !isInstalled && !isLoading) {
      setIsNewUser(true);
      localStorage.setItem(GITHUB_INSTALLATION_SEEN_KEY, "true");
    }
  }, [isInstalled, isLoading]);

  // Don't show banner if:
  // - Still loading installation status
  // - App is already installed
  // - User has dismissed the banner
  if (isLoading || isInstalled || dismissed) {
    return null;
  }

  const handleDismiss = () => {
    setDismissed(true);
    setIsNewUser(false);
  };

  // Enhanced messaging for new users
  const title = isNewUser
    ? "🎉 Welcome to Open SWE! Complete your setup"
    : "Complete your setup to start coding";

  const description = isNewUser
    ? "You're just one step away from AI-powered development! Install our GitHub App to connect your repositories and start coding with AI assistance."
    : "Install our GitHub App to grant access to your repositories and enable AI-powered development.";

  return (
    <InstallationPrompt
      title={title}
      description={description}
      variant="banner"
      showDismiss={true}
      onDismiss={handleDismiss}
      className={cn(isNewUser && "border-2 border-amber-300 shadow-lg")}
    />
  );
}
