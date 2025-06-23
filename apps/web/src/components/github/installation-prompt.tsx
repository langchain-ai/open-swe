"use client";

import { InstallAppButton } from "./install-app-button";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface InstallationPromptProps {
  title?: string;
  description?: string;
  showDismiss?: boolean;
  onDismiss?: () => void;
  className?: string;
  variant?: "default" | "banner";
}

export function InstallationPrompt({
  title = "GitHub App Not Installed",
  description = "You need to install our GitHub App to grant access to your repositories.",
  showDismiss = false,
  onDismiss,
  className = "",
  variant = "default",
}: InstallationPromptProps) {
  return (
    <div
      className={cn(
        "rounded-md border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/20",
        variant === "banner" && "flex items-center justify-between",
        className,
      )}
    >
      {variant === "banner" ? (
        <>
          <div>
            <h3 className="mb-1 font-medium text-amber-800 dark:text-amber-200">
              {title}
            </h3>
            <p className="text-sm text-amber-700 dark:text-amber-300">
              {description}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <InstallAppButton
              variant="default"
              size="sm"
              className="border-amber-600 bg-amber-600 text-white hover:bg-amber-700"
            >
              Install GitHub App
            </InstallAppButton>
            {showDismiss && onDismiss && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onDismiss}
                className="h-8 w-8 p-0 text-amber-600 hover:text-amber-800 dark:text-amber-400 dark:hover:text-amber-200"
              >
                <X className="h-4 w-4" />
              </Button>
            )}
          </div>
        </>
      ) : (
        <>
          <h3 className="mb-2 font-medium text-amber-800 dark:text-amber-200">
            {title}
          </h3>
          <p className="mb-4 text-sm text-amber-700 dark:text-amber-300">
            {description}
          </p>
          <InstallAppButton>Install GitHub App</InstallAppButton>
        </>
      )}
    </div>
  );
}
