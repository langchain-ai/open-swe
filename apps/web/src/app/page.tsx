"use client";

import { Toaster } from "@/components/ui/sonner";
import React, { useEffect } from "react";
import AuthStatus from "@/components/github/auth-status";
import { useRouter } from "next/navigation";
import { ENABLE_GITHUB } from "@openswe/shared/config";

export default function Page(): React.ReactNode {
  const router = useRouter();
  const githubDisabled = !ENABLE_GITHUB;

  useEffect(() => {
    if (githubDisabled) {
      router.replace("/chat");
    }
  }, [githubDisabled, router]);

  if (githubDisabled) {
    return (
      <React.Suspense fallback={<div>Loading (layout)...</div>}>
        <Toaster />
      </React.Suspense>
    );
  }

  return (
    <React.Suspense fallback={<div>Loading (layout)...</div>}>
      <Toaster />
      <div className="w-full">
        <AuthStatus />
      </div>
    </React.Suspense>
  );
}
