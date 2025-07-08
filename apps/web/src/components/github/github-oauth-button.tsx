"use client";

import { useState } from "react";
import { LogOut } from "lucide-react";
import { TooltipIconButton } from "../ui/tooltip-icon-button";

const GITHUB_APP_INSTALLED_KEY = "github_app_installed";

export function GitHubLogoutButton() {
  const [isLoading, setIsLoading] = useState(false);

  const handleLogout = async () => {
    setIsLoading(true);
    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST",
      });
      if (response.ok) {
        localStorage.removeItem(GITHUB_APP_INSTALLED_KEY);
        window.location.href = "/";
      } else {
        console.error("Logout failed");
      }
    } catch (error) {
      console.error("Error during logout:", error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <TooltipIconButton
      tooltip="Logout"
      onClick={handleLogout}
      disabled={isLoading}
      className="text-xs"
      size="sm"
    >
      <LogOut className="size-4" />
    </TooltipIconButton>
  );
}
