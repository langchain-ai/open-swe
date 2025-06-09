"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface ConfigSectionProps {
  title: string;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}

export function ConfigSection({
  title,
  children,
  className,
  action,
}: ConfigSectionProps) {
  return (
    <div className={cn("mb-6", className)}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-900">{title}</h3>
        {action}
      </div>
      <div className="space-y-4">{children}</div>
    </div>
  );
}
