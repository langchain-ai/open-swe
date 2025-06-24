"use client";

import { useState } from "react";
import { LogOut } from "lucide-react";
import { TooltipIconButton } from "../ui/tooltip-icon-button";

export function GitHubLogoutButton() {
  const [isLoading, setIsLoading] = useState(false);

  const handleLogout = async () => {
    setIsLoading(true);
    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST",
      });
      if (response.ok) {
        localStorage.removeItem("github_app_installed");
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
