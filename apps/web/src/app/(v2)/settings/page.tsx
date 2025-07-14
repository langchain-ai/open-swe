"use client";

import { Suspense } from "react";
import SettingsPage from "@/features/settings-page/index";

function SettingsPageLoading() {
  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-8">
        <h1 className="text-foreground mb-2 text-3xl font-bold">Settings</h1>
        <p className="text-muted-foreground">Loading your settings...</p>
      </div>
      <div className="space-y-6">
        <div className="bg-muted h-20 animate-pulse rounded"></div>
        <div className="bg-muted h-40 animate-pulse rounded"></div>
        <div className="bg-muted h-60 animate-pulse rounded"></div>
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<SettingsPageLoading />}>
      <SettingsPage />
    </Suspense>
  );
}
