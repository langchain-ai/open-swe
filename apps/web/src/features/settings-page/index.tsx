"use client";

import { useQueryState } from "nuqs";
import { useRouter } from "next/navigation";
import { Key, Settings, ArrowLeft } from "lucide-react";
import { APIKeysTab } from "./api-keys";
import { ConfigManager } from "./config-manager";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function SettingsPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useQueryState("tab", {
    defaultValue: "api-keys" as "api-keys" | "configuration",
    parse: (value: string) => {
      const allowed = ["api-keys", "configuration"];
      if (allowed.includes(value)) {
        return value as "api-keys" | "configuration";
      }
      return "api-keys";
    },
    serialize: (value) => value,
  });

  const getTabClassName = (isActive: boolean) =>
    cn(
      "relative border-b-2 px-4 py-3 text-sm font-medium transition-colors",
      isActive
        ? "border-primary bg-background text-primary"
        : "text-muted-foreground hover:bg-muted hover:text-foreground border-transparent",
    );

  return (
    <div className="mx-auto max-w-6xl p-6">
      {/* Header with back button and theme toggle */}
      <div className="mb-6 flex items-center justify-between">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push("/chat")}
          className="flex items-center gap-2"
        >
          <ArrowLeft className="size-4" />
          Back to Chat
        </Button>
        <ThemeToggle />
      </div>

      <div className="mb-8">
        <h1 className="text-foreground mb-2 text-3xl font-bold">Settings</h1>
        <p className="text-muted-foreground">Manage your API configurations</p>
      </div>

      <div className="mb-6">
        <div className="border-border bg-muted/50 flex rounded-t-lg border-b">
          <button
            onClick={() => setActiveTab("api-keys")}
            className={getTabClassName(activeTab === "api-keys")}
          >
            <span className="flex items-center gap-2 font-mono">
              <Key className="size-4" />
              API Keys
            </span>
          </button>
          <button
            onClick={() => setActiveTab("configuration")}
            className={getTabClassName(activeTab === "configuration")}
          >
            <span className="flex items-center gap-2 font-mono">
              <Settings className="size-4" />
              Configuration
            </span>
          </button>
        </div>
      </div>

      <div className="border-border bg-background rounded-b-lg border border-t-0 p-6">
        {activeTab === "api-keys" && <APIKeysTab />}
        {activeTab === "configuration" && <ConfigManager />}
      </div>
    </div>
  );
}
