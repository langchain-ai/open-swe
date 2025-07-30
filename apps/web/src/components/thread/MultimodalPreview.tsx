import React from "react";
import { File, X as XIcon } from "lucide-react";
import type { Base64ContentBlock } from "@langchain/core/messages";
import { cn } from "@/lib/utils";
import Image from "next/image";
export interface MultimodalPreviewProps {
  block: Base64ContentBlock;
  removable?: boolean;
  onRemove?: () => void;
  className?: string;
  size?: "sm" | "md" | "lg";
}

export const MultimodalPreview: React.FC<MultimodalPreviewProps> = ({
  block,
  removable = false,
  onRemove,
  className,
  size = "md",
}) => {
  // Image block
  if (
    block.type === "image" &&
    block.source_type === "base64" &&
    typeof block.mime_type === "string" &&
    block.mime_type.startsWith("image/")
  ) {
    const url = `data:${block.mime_type};base64,${block.data}`;
    let imgClass: string =
      "rounded-md object-cover h-16 w-16 text-lg shadow-sm ring-1 ring-border";
    if (size === "sm")
      imgClass =
        "rounded-md object-cover h-10 w-10 text-base shadow-sm ring-1 ring-border";
    if (size === "lg")
      imgClass =
        "rounded-md object-cover h-24 w-24 text-xl shadow-sm ring-1 ring-border";
    return (
      <div className={cn("relative inline-block", className)}>
        <Image
          src={url}
          alt={String(block.metadata?.name || "uploaded image")}
          className={imgClass}
          width={size === "sm" ? 16 : size === "md" ? 32 : 48}
          height={size === "sm" ? 16 : size === "md" ? 32 : 48}
        />
        {removable && (
          <button
            type="button"
            className="bg-destructive/90 text-destructive-foreground hover:bg-destructive absolute -top-2 -right-2 z-10 cursor-pointer rounded-full p-1 shadow-sm"
            onClick={onRemove}
            aria-label="Remove image"
          >
            <XIcon className="h-3 w-3" />
          </button>
        )}
      </div>
    );
  }

  // PDF block
  if (
    block.type === "file" &&
    block.source_type === "base64" &&
    block.mime_type === "application/pdf"
  ) {
    const filename =
      block.metadata?.filename || block.metadata?.name || "PDF file";
    return (
      <div
        className={cn(
          "border-border bg-card/50 relative flex items-center justify-start gap-2 rounded-md border px-3 py-2",
          className,
        )}
      >
        <div className="flex flex-shrink-0 flex-col items-start justify-start">
          <File
            className={cn(
              "text-primary",
              size === "sm" ? "h-5 w-5" : "h-7 w-7",
            )}
          />
        </div>
        <span
          className={cn("text-foreground min-w-0 flex-1 text-sm break-all")}
          style={{ wordBreak: "break-all", whiteSpace: "pre-wrap" }}
        >
          {String(filename)}
        </span>
        {removable && (
          <button
            type="button"
            className="text-muted-foreground hover:bg-accent hover:text-accent-foreground cursor-pointer self-start rounded-full p-1"
            onClick={onRemove}
            aria-label="Remove PDF"
          >
            <XIcon className="h-4 w-4" />
          </button>
        )}
      </div>
    );
  }

  // Fallback for unknown types
  return (
    <div
      className={cn(
        "border-border bg-card/50 text-muted-foreground flex items-center gap-2 rounded-md border px-3 py-2",
        className,
      )}
    >
      <File className="h-5 w-5 flex-shrink-0" />
      <span className="truncate text-xs">Unsupported file type</span>
      {removable && (
        <button
          type="button"
          className="text-muted-foreground hover:bg-accent hover:text-accent-foreground cursor-pointer self-start rounded-full p-1"
          onClick={onRemove}
          aria-label="Remove file"
        >
          <XIcon className="h-4 w-4" />
        </button>
      )}
    </div>
  );
};
