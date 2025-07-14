"use client";

import { useState } from "react";
import { Key } from "lucide-react";
import { GitHubManager } from "./github-manager";
import { APIKeysTab } from "./api-keys";
import { GitHubSVG } from "@/components/icons/github";
import { GitHubAppProvider } from "@/providers/GitHubApp";
import { cn } from "@/lib/utils";

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<"github" | "api-keys">("github");

  const getTabClassName = (isActive: boolean) =>
    cn(
      "relative border-b-2 px-4 py-3 text-sm font-medium transition-colors",
      isActive
        ? "border-primary bg-background text-primary"
        : "text-muted-foreground hover:bg-muted hover:text-foreground border-transparent",
    );

  return (
    <GitHubAppProvider>
      <div className="mx-auto max-w-6xl p-6">
        <div className="mb-8">
          <h1 className="text-foreground mb-2 text-3xl font-bold">Settings</h1>
          <p className="text-muted-foreground">
            Manage your integrations and API configurations
          </p>
        </div>

        <div className="mb-6">
          <div className="border-border bg-muted/50 flex rounded-t-lg border-b">
            <button
              onClick={() => setActiveTab("github")}
              className={getTabClassName(activeTab === "github")}
            >
              <span className="flex items-center gap-2 font-mono">
                <GitHubSVG
                  height="16"
                  width="16"
                />
                GitHub
              </span>
            </button>
            <button
              onClick={() => setActiveTab("api-keys")}
              className={getTabClassName(activeTab === "api-keys")}
            >
              <span className="flex items-center gap-2 font-mono">
                <Key className="size-4" />
                API Keys
              </span>
            </button>
          </div>
        </div>

        <div className="border-border bg-background rounded-b-lg border border-t-0 p-6">
          {activeTab === "github" && <GitHubManager />}
          {activeTab === "api-keys" && <APIKeysTab />}
        </div>
      </div>
    </GitHubAppProvider>
  );
}
