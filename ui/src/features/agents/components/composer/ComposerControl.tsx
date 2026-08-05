import { ChevronDown } from "lucide-react";

import type { ComponentProps } from "react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const composerControlClassName =
  "h-7 min-h-7 gap-1.5 px-2 text-muted-foreground/70 transition-none hover:text-foreground/80";

/** A button in the composer's bottom control row (model, plan mode, attach). */
export function ComposerControl({
  className,
  size = "sm",
  variant = "ghost",
  ...props
}: ComponentProps<typeof Button>) {
  return (
    <Button
      className={cn(composerControlClassName, className)}
      size={size}
      variant={variant}
      {...props}
    />
  );
}

export function ComposerControlIcon({ className, icon: Icon }: { className?: string; icon: LucideIcon }) {
  return <Icon aria-hidden="true" className={cn("size-3.5 shrink-0", className)} />;
}

export function ComposerControlChevron() {
  return (
    <ChevronDown
      aria-hidden="true"
      className="-mx-0.5 size-3 shrink-0 text-muted-foreground opacity-70"
      strokeWidth={2.25}
    />
  );
}
