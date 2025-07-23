"use client";

import { useState, useEffect } from "react";
import { X, Key } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useUser } from "@/hooks/useUser";
import { useConfigStore, DEFAULT_CONFIG_KEY } from "@/hooks/useConfigStore";
import { isAllowedUser } from "@open-swe/shared/github/allowed-users";
import Link from "next/link";

const API_KEY_BANNER_DISMISSED_KEY = "api_key_banner_dismissed";

export function ApiKeyBanner() {
  const { user, isLoading } = useUser();
  const { getConfig } = useConfigStore();
  const config = getConfig(DEFAULT_CONFIG_KEY);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    // Check if user has previously dismissed the banner
    const hasDismissed = localStorage.getItem(API_KEY_BANNER_DISMISSED_KEY);
    if (hasDismissed === "true") {
      setDismissed(true);
    }
  }, []);

  // Don't show banner if:
  // - Still loading user data
  // - User is not authenticated
  // - User has dismissed the banner
  if (isLoading || !user || dismissed) {
    return null;
  }

  // Check if user is in the allowed list
  const userIsAllowed = isAllowedUser(user.login);

  // If user is allowed, they don't need API keys
  if (userIsAllowed) {
    return null;
  }

  // Check if user has any API keys configured
  const apiKeys = config.apiKeys || {};
  const hasApiKeys = 
    apiKeys.anthropicApiKey || 
    apiKeys.openaiApiKey || 
    apiKeys.googleApiKey;

  // If user has API keys, don't show banner
  if (hasApiKeys) {
    return null;
  }

  const handleDismiss = () => {
    setDismissed(true);
    localStorage.setItem(API_KEY_BANNER_DISMISSED_KEY, "true");
  };

  return (
    <div
      className={cn(
        "rounded-md border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950/20",
        "flex items-center justify-between"
      )}
    >
      <div className="flex items-center gap-3">
        <Key className="h-5 w-5 text-blue-600 dark:text-blue-400" />
        <div>
          <h3 className="mb-1 font-medium text-blue-800 dark:text-blue-200">
            API Key Required
          </h3>
          <p className="text-sm text-blue-700 dark:text-blue-300">
            You need to add an API key to use Open SWE. Add your Anthropic, OpenAI, or Google API key to get started.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Link href="/settings?tab=api-keys">
          <Button
            variant="default"
            size="sm"
            className="border-blue-600 bg-blue-600 text-white hover:bg-blue-700"
          >
            Add API Keys
          </Button>
        </Link>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleDismiss}
          className="h-8 w-8 p-0 text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-200"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

