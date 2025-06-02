"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";

interface InstallAppButtonProps {
  variant?:
    | "default"
    | "outline"
    | "secondary"
    | "ghost"
    | "link"
    | "destructive";
  size?: "default" | "sm" | "lg" | "icon";
  className?: string;
  children?: React.ReactNode;
}

export function InstallAppButton({
  variant = "default",
  size = "default",
  className = "",
  children,
}: InstallAppButtonProps) {
  const [isLoading, setIsLoading] = useState(false);

  const handleInstall = () => {
    setIsLoading(true);
    window.location.href = "/api/github/installation";
  };

  return (
    <Button
      variant={variant}
      size={size}
      className={className}
      onClick={handleInstall}
      disabled={isLoading}
    >
      {isLoading ? (
        "Installing..."
      ) : (
        <>
          <GitHubSVG
            width="16"
            height="16"
          />
          {children || "Install GitHub App"}
        </>
      )}
    </Button>
  );
}
