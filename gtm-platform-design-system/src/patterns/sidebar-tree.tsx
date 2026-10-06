"use client";

/*
 * Rules for sidebar trees, shared by the Agent rail and memory browser.
 * Agent folder headings use their identity icon; sections use a trailing chevron.
 * File directories use sentence-case rows, folder icons, a trailing disclosure
 * chevron and an inset guide for their children. Hosts own selection and actions.
 * Rows take the control rung for one line and record rung for a preview.
 * Disclosure motion and reduced-motion behavior belong to Collapsible.
 */

import type { ComponentProps, ReactNode } from "react";

import { Box, Inline } from "../ui/box";
import { cn } from "../ui/cn";
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "../ui/collapsible";
import { Folder, FolderOpen, type Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";

function SidebarTreeGroup({
  label,
  kind = "folder",
  glyph,
  glyphClassName,
  trailing,
  open,
  title,
  children,
  className,
  ...props
}: ComponentProps<typeof Collapsible> & {
  label: string;
  kind?: "folder" | "section" | "directory";
  glyph?: Glyph;
  glyphClassName?: string;
  trailing?: ReactNode;
  open: boolean;
}) {
  return (
    <Collapsible
      {...props}
      data-sidebar-tree-group=""
      open={open}
      className={cn("flex w-full flex-col gap-0.5 rounded-compact", className)}
    >
      <Inline gap="xs" align="center" className="group/sidebar-heading min-w-0">
        <CollapsibleTrigger
          aria-label={label}
          title={title}
          className={cn(
            "min-w-0 flex-1 rounded-compact hover:bg-hover",
            kind === "directory" ? "h-control gap-2 px-2" : "h-control-sm gap-1 px-1"
          )}
        >
          {kind !== "section" ? (
            <Icon icon={glyph ?? (open ? FolderOpen : Folder)} size="sm" className={glyphClassName ?? "text-ink-subtle"} />
          ) : null}
          <Box render={<span />} className={cn(
            "min-w-0 truncate font-medium",
            kind === "directory" ? "flex-1 text-label text-ink" : "text-meta tracking-caps text-ink-subtle uppercase"
          )}>
            {label}
          </Box>
          {kind !== "folder" ? <CollapsibleChevron /> : null}
        </CollapsibleTrigger>
        {trailing}
      </Inline>
      <CollapsibleContent>
        {kind === "directory" ? (
          <Box className="ml-4 border-l border-line-strong pl-2">{children}</Box>
        ) : children}
      </CollapsibleContent>
    </Collapsible>
  );
}

function SidebarTreeRow({
  selected,
  multiline,
  className,
  ...props
}: ComponentProps<typeof Box> & { selected?: boolean; multiline: boolean }) {
  return (
    <Box
      {...props}
      data-sidebar-tree-row=""
      padding="sm"
      className={cn(
        "relative flex w-full flex-col justify-center rounded-compact outline-none hover:bg-hover focus-within:bg-hover",
        multiline ? "min-h-row-record" : "min-h-control",
        selected && "bg-selected",
        className
      )}
    />
  );
}

export { SidebarTreeGroup, SidebarTreeRow };
