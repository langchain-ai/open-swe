"use client";

import { Toaster } from "@/components/ui/sonner";
import React from "react";
import AuthStatus from "@/components/github/auth-status";

export default function Page(): React.ReactNode {
  return (
    <React.Suspense fallback={<div>Loading (layout)...</div>}>
      <Toaster />
      <div className="w-full">
        <AuthStatus />
      </div>
    </React.Suspense>
  );
}
