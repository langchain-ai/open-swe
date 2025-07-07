"use client";

import { useState } from "react";
import { Key } from "lucide-react";
import { GitHubManager } from "./github-manager";
import { APIKeysTab } from "./api-keys";
import { GitHubSVG } from "@/components/icons/github";

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<"github" | "api-keys">("github");

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-8">
        <h1 className="mb-2 text-3xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-600">
          Manage your integrations and API configurations
        </p>
      </div>

      <div className="mb-6">
        <div className="flex rounded-t-lg border-b border-gray-200 bg-gray-50">
          <button
            onClick={() => setActiveTab("github")}
            className={`relative border-b-2 px-4 py-3 text-sm font-medium transition-colors ${
              activeTab === "github"
                ? "border-blue-500 bg-white text-blue-600"
                : "border-transparent text-gray-600 hover:bg-gray-100 hover:text-gray-800"
            }`}
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
            className={`relative border-b-2 px-4 py-3 text-sm font-medium transition-colors ${
              activeTab === "api-keys"
                ? "border-blue-500 bg-white text-blue-600"
                : "border-transparent text-gray-600 hover:bg-gray-100 hover:text-gray-800"
            }`}
          >
            <span className="flex items-center gap-2 font-mono">
              <Key className="size-4" />
              API Keys
            </span>
          </button>
        </div>
      </div>

      {/* Tab Content */}
      <div className="rounded-b-lg border border-t-0 border-gray-200 bg-white p-6">
        {activeTab === "github" && <GitHubManager />}
        {activeTab === "api-keys" && <APIKeysTab />}
      </div>
    </div>
  );
}
