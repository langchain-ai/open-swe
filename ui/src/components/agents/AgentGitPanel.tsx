import { useMemo, useState } from "react";
import { MultiFileDiff } from "@pierre/diffs/react";
import { CaretDownIcon, GitPullRequestIcon } from "@phosphor-icons/react";

import { diffOptions } from "@/components/agents/utils/diffUtils";
import type { AgentThread } from "@/lib/agents/types";
import { cn } from "@/lib/utils";

interface AgentGitPanelProps {
  thread: AgentThread;
}

export function AgentGitPanel({ thread }: AgentGitPanelProps) {
  const [tab, setTab] = useState<"diff" | "review" | "commits">("diff");
  const pr = thread.pr;

  const fileContents = useMemo(() => {
    const defaults: Record<string, { original: string; modified: string }> = {
      "app.js": {
        original: "export function init() {\n  mount();\n}\n",
        modified: "export function init() {\n  // test comment\n  mount();\n}\n",
      },
      "contextPanel.js": {
        original: "export function renderPanel() {\n  return panel;\n}\n",
        modified: "export function renderPanel() {\n  // test comment\n  return panel;\n}\n",
      },
      "settings.js": {
        original: "export const defaults = {};\n",
        modified: "export const defaults = {};\n// test comment\n",
      },
    };

    return (thread.changedFiles ?? []).map((file) => ({
      path: file.path,
      additions: file.additions,
      original: defaults[file.path]?.original ?? "",
      modified: defaults[file.path]?.modified ?? file.patch ?? "",
    }));
  }, [thread.changedFiles]);

  return (
    <aside className="flex h-full w-[420px] shrink-0 flex-col border-l border-[var(--ui-border)] bg-[var(--ui-surface)]">
      <div className="flex h-11 shrink-0 items-center gap-1 border-b border-[var(--ui-border)] px-3">
        {(["Git", "Desktop", "Terminal"] as const).map((label, i) => (
          <button
            key={label}
            type="button"
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors",
              i === 0
                ? "bg-[var(--ui-accent-bubble)] font-medium text-[var(--ui-text)]"
                : "text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)]",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {pr && (
        <div className="border-b border-[var(--ui-border)] px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-[var(--ui-text)]">
                {pr.title} #{pr.number}
              </div>
              <div className="mt-1 flex items-center gap-2 text-[11px] text-[var(--ui-text-dim)]">
                <span className="inline-flex items-center gap-1 rounded border border-[var(--ui-border)] px-1.5 py-0.5 capitalize">
                  <GitPullRequestIcon className="size-3" />
                  {pr.state}
                </span>
                <span>
                  {pr.headRef} → {pr.baseRef}
                </span>
              </div>
            </div>
            <button
              type="button"
              className="shrink-0 rounded-md border border-[var(--ui-border)] px-2.5 py-1 text-xs hover:bg-[var(--ui-panel-2)]"
            >
              Mark as ready
            </button>
          </div>
        </div>
      )}

      <div className="flex items-center gap-1 border-b border-[var(--ui-border)] px-3 py-2">
        {(
          [
            ["diff", "Diff"],
            ["review", "Review"],
            ["commits", `Commits (${fileContents.length})`],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors",
              tab === id
                ? "bg-[var(--ui-accent-bubble)] font-medium text-[var(--ui-text)]"
                : "text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)]",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "diff" && fileContents.length > 0 ? (
          <div className="space-y-2 p-2">
            {fileContents.map((file) => (
              <FileDiffSection key={file.path} file={file} />
            ))}
          </div>
        ) : (
          <div className="p-6 text-center text-xs text-[var(--ui-text-dim)]">
            {tab === "commits" ? "Commit history will appear here." : "No diff available."}
          </div>
        )}
      </div>
    </aside>
  );
}

function FileDiffSection({
  file,
}: {
  file: { path: string; additions: number; original: string; modified: string };
}) {
  const [open, setOpen] = useState(true);

  return (
    <div className="mb-2 overflow-hidden rounded-lg border border-[var(--ui-border)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 bg-[var(--ui-panel-2)] px-3 py-2 text-left text-xs"
      >
        <CaretDownIcon className={cn("size-3 transition-transform", !open && "-rotate-90")} />
        <span className="font-medium text-[var(--ui-text)]">{file.path}</span>
        <span className="ml-auto text-[var(--ui-success)]">+{file.additions}</span>
      </button>
      {open && (
        <div className="max-h-[320px] overflow-auto bg-[var(--ui-panel)] p-2 font-mono text-[11px] leading-5">
          <MultiFileDiff
            oldFile={{ name: file.path, contents: file.original }}
            newFile={{ name: file.path, contents: file.modified }}
            options={diffOptions}
          />
        </div>
      )}
    </div>
  );
}
