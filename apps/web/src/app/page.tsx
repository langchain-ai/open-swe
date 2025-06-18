"use client";

import { Thread } from "@/components/thread";
import { StreamProvider } from "@/providers/Stream";
import { ThreadProvider } from "@/providers/Thread";
import { Toaster } from "@/components/ui/sonner";
import React from "react";
import { GitHubAppProvider } from "@/providers/GitHubApp";

export default function DemoPage(): React.ReactNode {
  return (
    <React.Suspense fallback={<div>Loading (layout)...</div>}>
      <Toaster />
      <GitHubAppProvider>
        <ThreadProvider>
          <StreamProvider>
            <Thread />
          </StreamProvider>
        </ThreadProvider>
      </GitHubAppProvider>
    </React.Suspense>
  );
}
