import { ImageIcon, MicrophoneIcon, PaperPlaneTiltIcon } from "@phosphor-icons/react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

const MODELS = ["GPT-5.5 High", "Opus 4.6 High", "Composer 2.5 Fast"];

interface AgentPromptBarProps {
  placeholder?: string;
  compact?: boolean;
  onSubmit?: (value: string) => void;
}

export function AgentPromptBar({
  placeholder = "Ask Cursor to build, fix bugs, explore",
  compact = false,
  onSubmit,
}: AgentPromptBarProps) {
  const [value, setValue] = useState("");
  const [model, setModel] = useState(MODELS[0]);

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSubmit?.(trimmed);
    setValue("");
  };

  return (
    <div className={cn("w-full", compact ? "max-w-none" : "max-w-2xl")}>
      <div
        className={cn(
          "rounded-2xl border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-sm",
          compact ? "px-3 py-2" : "px-4 py-3",
        )}
      >
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          rows={compact ? 1 : 3}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSubmit();
            }
          }}
          className={cn(
            "w-full resize-none bg-transparent text-sm text-[var(--ui-text)] outline-none placeholder:text-[var(--ui-text-dim)]",
            compact ? "min-h-[24px]" : "min-h-[72px]",
          )}
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Select value={model} onValueChange={(v) => v && setModel(v)}>
              <SelectTrigger className="h-8 border-[var(--ui-border)] bg-[var(--ui-panel)] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODELS.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 border-[var(--ui-border)] text-xs text-[var(--ui-text-muted)]"
            >
              Enable agent demos
            </Button>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              className="flex size-8 items-center justify-center rounded-md text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)]"
              aria-label="Attach image"
            >
              <ImageIcon className="size-4" />
            </button>
            <button
              type="button"
              className="flex size-8 items-center justify-center rounded-md text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)]"
              aria-label="Voice input"
            >
              <MicrophoneIcon className="size-4" />
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              className="flex size-8 items-center justify-center rounded-md bg-[var(--ui-accent)] text-white hover:opacity-90"
              aria-label="Send"
            >
              <PaperPlaneTiltIcon className="size-4" weight="fill" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
