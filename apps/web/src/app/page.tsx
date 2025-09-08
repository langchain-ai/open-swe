"use client";

import { Toaster } from "@/components/ui/sonner";
import React, { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function Page(): React.ReactNode {
  const router = useRouter();

  useEffect(() => {
    router.replace("/chat");
  }, [router]);

  return (
    <React.Suspense fallback={<div>Loading (layout)...</div>}>
      <Toaster />
    </React.Suspense>
  );
}
