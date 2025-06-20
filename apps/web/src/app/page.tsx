"use client";

import { Thread } from "@/components/thread";
import { ThreadProvider } from "@/providers/Thread";
import { StreamProvider } from "@/providers/Stream";
import { Toaster } from "@/components/ui/sonner";
import React from "react";
import { GitHubAppProvider } from "@/providers/GitHubApp";

function getCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
  return match ? decodeURIComponent(match[2]) : undefined;
}

export default function DemoPage(): React.ReactNode {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  const assistantId = process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "";
  const githubToken =
    typeof window !== "undefined" ? getCookie("github_token") : "";

  return (
    <React.Suspense fallback={<div>Loading (layout)...</div>}>
      <Toaster />
      <GitHubAppProvider>
        <ThreadProvider>
          <StreamProvider
            apiUrl={apiUrl}
            assistantId={assistantId}
            githubToken={githubToken || ""}
          >
            <Thread />
          </StreamProvider>
        </ThreadProvider>
      </GitHubAppProvider>
    </React.Suspense>
  );
}
