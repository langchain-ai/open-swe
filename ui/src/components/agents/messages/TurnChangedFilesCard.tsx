import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { MultiFileDiff } from "@pierre/diffs/react";

import { useDiffOptions, warmDiffHighlighter } from "@/components/agents/utils/diffUtils";

import type { ChangedFileSummaryItem } from "./types";

function stripProjectPathForDisplay(path: string, projectPath?: string): string {
  if (!projectPath) return path;
  const normalizedPath = path.replace(/\\/g, "/");
  const normalizedProjectPath = projectPath.replace(/\\/g, "/").replace(/\/+$/, "");
  if (!normalizedPath.startsWith(`${normalizedProjectPath}/`)) return path;
  return normalizedPath.slice(normalizedProjectPath.length + 1);
}

const ChangedFileRow = memo(function ChangedFileRow({
  file,
  projectPath,
  open,
  diffReady,
  onToggle,
}: {
  file: ChangedFileSummaryItem;
  projectPath?: string;
  open: boolean;
  diffReady: boolean;
  onToggle: (filePath: string) => void;
}) {
  const diffOptions = useDiffOptions();
  const displayPath = useMemo(
    () => stripProjectPathForDisplay(file.filePath, projectPath),
    [file.filePath, projectPath],
  );
  const oldFile = useMemo(
    () => ({ name: displayPath, contents: file.originalContent }),
    [displayPath, file.originalContent],
  );
  const newFile = useMemo(
    () => ({ name: displayPath, contents: file.modifiedContent }),
    [displayPath, file.modifiedContent],
  );

  return (
    <div className="border-b last:border-b-0 border-[var(--ui-border)]">
      <button
        type="button"
        onClick={() => onToggle(file.filePath)}
        className="w-full px-3 py-2 text-left hover:bg-[var(--ui-panel-2)] transition-colors flex items-center justify-between gap-3"
        aria-expanded={open}
      >
        <span className="text-[13px] text-[color:var(--ui-accent)] truncate min-w-0">{displayPath}</span>
        <span className="shrink-0 flex items-center gap-2">
          <span className="text-xs flex items-center gap-2">
            <span className="text-green-400">+{file.additions}</span>
            <span className="text-red-400">-{file.deletions}</span>
          </span>
          {open ? (
            <ChevronUp className="h-3.5 w-3.5 text-[color:var(--ui-text-dim)] shrink-0" aria-hidden />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 text-[color:var(--ui-text-dim)] shrink-0" aria-hidden />
          )}
        </span>
      </button>
      {open && (
        <div className="px-2 pb-2">
          <div className="rounded-lg bg-[var(--ui-panel)] overflow-hidden border border-[var(--ui-border-subtle)]">
            {diffReady ? (
              <div className="max-h-[250px] overflow-auto">
                <MultiFileDiff oldFile={oldFile} newFile={newFile} options={diffOptions} />
              </div>
            ) : (
              <div className="px-3 py-4 text-xs text-[color:var(--ui-text-dim)]">Loading diff…</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
});

export const TurnChangedFilesCard = memo(function TurnChangedFilesCard({
  files,
  totals,
  projectPath,
}: {
  files: ChangedFileSummaryItem[];
  totals: { additions: number; deletions: number };
  projectPath?: string;
}) {
  const [expandedByPath, setExpandedByPath] = useState<Record<string, boolean>>({});
  const [diffReady, setDiffReady] = useState(false);

  useEffect(() => {
    let active = true;
    warmDiffHighlighter().finally(() => {
      if (active) setDiffReady(true);
    });
    return () => {
      active = false;
    };
  }, []);

  const toggleFile = useCallback((filePath: string) => {
    setExpandedByPath((prev) => ({ ...prev, [filePath]: !prev[filePath] }));
  }, []);

  return (
    <div className="mt-3 rounded-xl bg-[var(--ui-accent-bubble)] overflow-hidden">
      <div className="px-3 py-2 text-xs text-[color:var(--ui-text-muted)] border-b border-[var(--ui-border)] flex items-center gap-2">
        <span>{files.length} file{files.length === 1 ? "" : "s"} changed</span>
        <span className="text-green-400">+{totals.additions}</span>
        <span className="text-red-400">-{totals.deletions}</span>
      </div>
      <div>
        {files.map((file) => (
          <ChangedFileRow
            key={file.filePath}
            file={file}
            projectPath={projectPath}
            open={!!expandedByPath[file.filePath]}
            diffReady={diffReady}
            onToggle={toggleFile}
          />
        ))}
      </div>
    </div>
  );
});
