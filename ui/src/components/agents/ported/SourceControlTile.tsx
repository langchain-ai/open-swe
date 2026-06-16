// Ported desktop git panel — calls host `window.git`, not used by the web dashboard
// (see AgentGitPanel). Excluded from tsconfig until integrated; see ui/tsconfig.json.
import { useState, useCallback, useRef, memo, useMemo, useEffect } from "react";
import { MultiFileDiff } from "@pierre/diffs/react";
import type { FileContents } from "@pierre/diffs/react";
import type { GitStatusEntry, GitFileStatus } from "@/lib/agents/types";
import { countLineChanges } from "@/components/agents/utils/diffStats";
import {
  ChevronDown,
  ChevronRight,
  ChevronLeft,
  Plus,
  Minus,
  GitCommit,
  Upload,
  Download,
  GitPullRequest,
  GitBranch,
  RefreshCw,
  FileText,
  Folder,
  FolderOpen,
  X,
  Undo2,
} from "lucide-react";
import { useDiffOptions } from "@/components/agents/utils/diffUtils";

const STATUS_LABELS: Partial<Record<GitFileStatus, string>> = {
  "index-modified": "M",
  "index-added": "A",
  "index-deleted": "D",
  "index-renamed": "R",
  "index-copied": "C",
  modified: "M",
  deleted: "D",
  untracked: "U",
  ignored: "!",
  "type-changed": "T",
  "intent-to-add": "A",
  "both-modified": "!",
  "both-added": "!",
  "added-by-us": "!",
  "added-by-them": "!",
  "deleted-by-us": "!",
  "deleted-by-them": "!",
  "both-deleted": "!",
};

const STATUS_COLORS: Partial<Record<GitFileStatus, string>> = {
  "index-modified": "text-yellow-400",
  "index-added": "text-green-400",
  "index-deleted": "text-red-400",
  "index-renamed": "text-gray-400",
  "index-copied": "text-gray-400",
  modified: "text-yellow-400",
  deleted: "text-red-400",
  untracked: "text-green-400",
  ignored: "text-gray-500",
  "type-changed": "text-yellow-400",
  "intent-to-add": "text-green-400",
  "both-modified": "text-orange-400",
  "both-added": "text-orange-400",
  "added-by-us": "text-orange-400",
  "added-by-them": "text-orange-400",
  "deleted-by-us": "text-orange-400",
  "deleted-by-them": "text-orange-400",
  "both-deleted": "text-orange-400",
};

interface SourceControlPanelProps {
  projectPath?: string;
  mainProjectPath?: string;
}

type SyncStatus = {
  ahead: number;
  behind: number;
  remote: string | null;
  branchName: string | null;
};

interface FileDiff {
  path: string;
  entry: GitStatusEntry;
  original: string;
  modified: string;
  additions: number;
  deletions: number;
}

type FileTreeNode = {
  name: string;
  path: string;
  children: FileTreeNode[];
  diff?: FileDiff;
};

function isUntracked(status: GitFileStatus): boolean {
  return status === "untracked";
}

function isConflict(status: GitFileStatus): boolean {
  return (
    status === "both-modified" ||
    status === "both-added" ||
    status === "both-deleted" ||
    status === "added-by-us" ||
    status === "added-by-them" ||
    status === "deleted-by-us" ||
    status === "deleted-by-them"
  );
}

function countChanges(
  oldContent: string,
  newContent: string,
  filePath: string,
): { additions: number; deletions: number } {
  return countLineChanges(oldContent, newContent, filePath);
}

function stripProjectPath(path: string, projectPath?: string): string {
  if (!projectPath) return path;
  const normalizedPath = path.replace(/\\/g, "/");
  const normalizedProjectPath = projectPath
    .replace(/\\/g, "/")
    .replace(/\/+$/, "");
  if (!normalizedPath.startsWith(`${normalizedProjectPath}/`)) return path;
  return normalizedPath.slice(normalizedProjectPath.length + 1);
}

export const SourceControlPanel = memo(function SourceControlPanel({
  projectPath,
  mainProjectPath,
}: SourceControlPanelProps) {
  const FILE_EXPLORER_MIN_WIDTH = 220;
  const FILE_EXPLORER_MAX_WIDTH = 520;
  const activePath = projectPath;
  const rootPath = mainProjectPath || projectPath;

  const [entries, setEntries] = useState<GitStatusEntry[]>([]);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [fileDiffs, setFileDiffs] = useState<FileDiff[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{
    text: string;
    error: boolean;
  } | null>(null);
  const statusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [fileExplorerOpen, setFileExplorerOpen] = useState(true);
  const [fileExplorerWidth, setFileExplorerWidth] = useState(288);
  const [gitActionsOpen, setGitActionsOpen] = useState(false);
  const [commitDialogOpen, setCommitDialogOpen] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const [committing, setCommitting] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [createBranchOpen, setCreateBranchOpen] = useState(false);
  const [newBranchName, setNewBranchName] = useState("");
  const [selectedDiffPath, setSelectedDiffPath] = useState<string | null>(null);

  const gitActionsRef = useRef<HTMLDivElement>(null);
  const diffCardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const resizeCleanupRef = useRef<(() => void) | null>(null);

  const showStatus = useCallback((text: string, error = false) => {
    setStatusMsg({ text, error });
    if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
    statusTimerRef.current = setTimeout(() => setStatusMsg(null), 3000);
  }, []);

  const loadDiffs = useCallback(
    async (statusEntries: GitStatusEntry[]) => {
      if (!activePath) return;
      const allEntries = statusEntries.filter((e) => !isConflict(e.status));
      const diffs: FileDiff[] = [];

      await Promise.all(
        allEntries.map(async (entry) => {
          try {
            const diff = await window.git.diffFile(
              activePath,
              entry.path,
              entry.staged
            );
            if (diff) {
              const { additions, deletions } = countChanges(
                diff.original,
                diff.modified,
                entry.path,
              );
              diffs.push({
                path: entry.path,
                entry,
                original: diff.original,
                modified: diff.modified,
                additions,
                deletions,
              });
            }
          } catch {
            // skip files that fail to diff
          }
        })
      );

      diffs.sort((a, b) => a.path.localeCompare(b.path));
      setFileDiffs(diffs);
    },
    [activePath]
  );

  const refresh = useCallback(async () => {
    if (!activePath) return;
    setLoading(true);
    try {
      const [statusEntries, sync] = await Promise.all([
        window.git.status(activePath),
        window.git.syncStatus(activePath),
      ]);
      setEntries(statusEntries);
      setSyncStatus(sync);
      await loadDiffs(statusEntries);
    } catch {
      setEntries([]);
      setSyncStatus(null);
      setFileDiffs([]);
    }
    setLoading(false);
  }, [activePath, loadDiffs]);

  // Initial load + polling
  const initialLoadDone = useRef(false);
  if (!initialLoadDone.current && activePath) {
    initialLoadDone.current = true;
    refresh();
  }

  // Poll for status changes (not full diffs) every 3s
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const activePollPath = useRef(activePath);

  if (activePollPath.current !== activePath) {
    activePollPath.current = activePath;
    initialLoadDone.current = false;
    if (activePath) {
      initialLoadDone.current = true;
      refresh();
    }
  }

  // Setup polling via useState init (runs once)
  useState(() => {
    pollRef.current = setInterval(async () => {
      const path = activePollPath.current;
      if (!path) return;
      try {
        const [statusEntries, sync] = await Promise.all([
          window.git.status(path),
          window.git.syncStatus(path),
        ]);
        setEntries((prev) => {
          const json = JSON.stringify(statusEntries);
          if (json === JSON.stringify(prev)) return prev;
          // Diffs will be loaded on-demand or via manual refresh
          return statusEntries;
        });
        setSyncStatus(sync);
      } catch {
        // ignore polling errors
      }
    }, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  });

  // Categorize entries
  const stagedEntries = useMemo(
    () => entries.filter((e) => e.staged && !isConflict(e.status)),
    [entries]
  );
  const changedEntries = useMemo(
    () =>
      entries.filter(
        (e) => !e.staged && !isConflict(e.status) && !isUntracked(e.status)
      ),
    [entries]
  );
  const untrackedEntries = useMemo(
    () => entries.filter((e) => isUntracked(e.status)),
    [entries]
  );
  const totalUnstaged = changedEntries.length + untrackedEntries.length;
  const totalChanges = entries.length;

  // Handlers
  const handleStageAll = useCallback(async () => {
    if (!activePath) return;
    const result = await window.git.stageAll(activePath);
    if (!result.success) showStatus(result.error ?? "Stage all failed", true);
    else refresh();
  }, [activePath, refresh, showStatus]);

  const handleDiscardAll = useCallback(async () => {
    if (!activePath) return;
    const result = await window.git.discardAll(activePath);
    if (!result.success)
      showStatus(result.error ?? "Discard all failed", true);
    else refresh();
  }, [activePath, refresh, showStatus]);

  const handleUnstageAll = useCallback(async () => {
    if (!activePath) return;
    const result = await window.git.unstageAll(activePath);
    if (!result.success)
      showStatus(result.error ?? "Unstage all failed", true);
    else refresh();
  }, [activePath, refresh, showStatus]);

  const handleCommit = useCallback(async () => {
    if (!activePath || !commitMessage.trim()) return;
    if (stagedEntries.length === 0) {
      showStatus("No staged changes to commit", true);
      return;
    }
    setCommitting(true);
    const result = await window.git.commit(activePath, commitMessage.trim());
    setCommitting(false);
    if (result.success) {
      setCommitMessage("");
      setCommitDialogOpen(false);
      showStatus("Committed successfully");
      refresh();
    } else {
      showStatus(result.error ?? "Commit failed", true);
    }
  }, [activePath, commitMessage, stagedEntries.length, refresh, showStatus]);

  const handlePush = useCallback(async () => {
    if (!activePath) return;
    setPushing(true);
    const result = await window.git.push(activePath);
    setPushing(false);
    if (result.success) {
      showStatus("Pushed successfully");
      setGitActionsOpen(false);
      refresh();
    } else {
      showStatus(result.error ?? "Push failed", true);
    }
  }, [activePath, refresh, showStatus]);

  const handlePull = useCallback(async () => {
    if (!activePath) return;
    setPulling(true);
    const result = await window.git.pull(activePath);
    setPulling(false);
    if (result.success) {
      showStatus("Pulled successfully");
      setGitActionsOpen(false);
      refresh();
    } else {
      showStatus(result.error ?? "Pull failed", true);
    }
  }, [activePath, refresh, showStatus]);

  const handleCreatePR = useCallback(async () => {
    if (!activePath) return;
    const result = await window.git.createPR(activePath);
    setGitActionsOpen(false);
    if (!result.success) {
      showStatus(result.error ?? "Failed to create PR", true);
    } else {
      showStatus("Opening PR in browser...");
    }
  }, [activePath, showStatus]);

  const handleCreateBranch = useCallback(async () => {
    if (!activePath || !newBranchName.trim()) return;
    const result = await window.git.createBranch(
      activePath,
      newBranchName.trim()
    );
    if (result.success) {
      setNewBranchName("");
      setCreateBranchOpen(false);
      setGitActionsOpen(false);
      showStatus(`Created branch: ${newBranchName.trim()}`);
      refresh();
    } else {
      showStatus(result.error ?? "Failed to create branch", true);
    }
  }, [activePath, newBranchName, refresh, showStatus]);

  // Build diff cards data
  const diffCards = useMemo(() => {
    return fileDiffs.map((fd) => {
      const displayPath = stripProjectPath(fd.path, activePath);
      const oldFile: FileContents = {
        name: displayPath,
        contents: fd.original,
      };
      const newFile: FileContents = {
        name: displayPath,
        contents: fd.modified,
      };
      return {
        key: `${fd.entry.staged ? "s" : "c"}-${fd.path}`,
        displayPath,
        oldFile,
        newFile,
        additions: fd.additions,
        deletions: fd.deletions,
      };
    });
  }, [fileDiffs, activePath]);

  const fileTree = useMemo(() => buildFileTree(fileDiffs, activePath), [fileDiffs, activePath]);
  const diffPaths = useMemo(
    () => diffCards.map((card) => card.displayPath),
    [diffCards]
  );
  const effectiveSelectedDiffPath =
    selectedDiffPath && diffPaths.includes(selectedDiffPath)
      ? selectedDiffPath
      : diffPaths[0] ?? null;

  const focusDiff = useCallback((path: string) => {
    setSelectedDiffPath(path);
    diffCardRefs.current[path]?.scrollIntoView({
      block: "start",
      behavior: "smooth",
    });
  }, []);

  const handleResizeStart = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = fileExplorerWidth;

      const handleMouseMove = (moveEvent: MouseEvent) => {
        const nextWidth = startWidth + (moveEvent.clientX - startX);
        setFileExplorerWidth(
          Math.min(
            FILE_EXPLORER_MAX_WIDTH,
            Math.max(FILE_EXPLORER_MIN_WIDTH, nextWidth)
          )
        );
      };

      const handleMouseUp = () => {
        document.removeEventListener("mousemove", handleMouseMove);
        document.removeEventListener("mouseup", handleMouseUp);
        resizeCleanupRef.current = null;
      };

      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
      resizeCleanupRef.current = handleMouseUp;
    },
    [FILE_EXPLORER_MAX_WIDTH, FILE_EXPLORER_MIN_WIDTH, fileExplorerWidth]
  );

  useEffect(() => {
    return () => {
      resizeCleanupRef.current?.();
    };
  }, []);

  const branchName = syncStatus?.branchName ?? "";
  const aheadBehind = syncStatus
    ? (() => {
      const { ahead, behind } = syncStatus;
      if (ahead > 0 && behind > 0) return `${ahead}↑ ${behind}↓`;
      if (ahead > 0) return `${ahead}↑`;
      if (behind > 0) return `${behind}↓`;
      return null;
    })()
    : null;

  return (
    <div
      className="relative flex flex-col h-full w-full bg-[var(--ui-bg)] text-[color:var(--ui-text)] overflow-hidden"
      onClick={() => {
        if (gitActionsOpen) setGitActionsOpen(false);
      }}
    >
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 h-9 shrink-0 border-b border-[var(--ui-border)]">
        <div className="flex items-center gap-2">
          <GitBranch size={14} className="text-[color:var(--ui-text-dim)]" />
          {branchName && (
            <span className="text-xs text-[color:var(--ui-text-muted)]">
              {branchName}
            </span>
          )}
          {aheadBehind && (
            <span className="text-[10px] text-[color:var(--ui-text-dim)] tabular-nums">
              {aheadBehind}
            </span>
          )}
          {totalChanges > 0 && (
            <span className="text-[10px] text-[color:var(--ui-text-dim)]">
              {totalChanges} change{totalChanges === 1 ? '' : 's'}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {totalChanges > 0 && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setFileExplorerOpen(!fileExplorerOpen);
              }}
              className="flex items-center gap-1.5 text-[color:var(--ui-text-dim)] hover:text-[color:var(--ui-text)] transition-colors px-2 py-1 rounded text-xs"
              title={fileExplorerOpen ? "Hide changed files" : "Show changed files"}
            >
              <FileText size={12} />
              <span>Changed Files</span>
              <span className="text-[10px] opacity-60 tabular-nums">
                {totalChanges}
              </span>
              {fileExplorerOpen ? (
                <ChevronLeft size={12} />
              ) : (
                <ChevronRight size={12} />
              )}
            </button>
          )}
          {/* Git Actions dropdown - kept for commit/branch which need input */}
          <div className="relative" ref={gitActionsRef}>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setGitActionsOpen(!gitActionsOpen);
              }}
              className="flex items-center gap-1 text-[color:var(--ui-text-dim)] hover:text-[color:var(--ui-text)] transition-colors px-2 py-1 rounded text-xs"
              title="Git Actions"
            >
              Git Actions
              <ChevronDown size={12} />
            </button>
            {gitActionsOpen && (
              <div
                className="absolute right-0 top-full mt-1 w-52 bg-[var(--ui-panel)] border border-[var(--ui-border)] rounded-lg shadow-xl z-50 py-1 overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  onClick={() => {
                    setCommitDialogOpen(true);
                    setGitActionsOpen(false);
                  }}
                  className="w-full px-3 py-2 text-left text-xs hover:bg-[var(--ui-panel-2)] transition-colors flex items-center gap-2 text-[color:var(--ui-text)]"
                >
                  <GitCommit size={13} />
                  Commit Changes
                  {stagedEntries.length > 0 && (
                    <span className="ml-auto text-[10px] text-[color:var(--ui-text-dim)] opacity-60">
                      {stagedEntries.length} staged
                    </span>
                  )}
                </button>
                <button
                  onClick={handlePush}
                  disabled={pushing}
                  className="w-full px-3 py-2 text-left text-xs hover:bg-[var(--ui-panel-2)] transition-colors flex items-center gap-2 text-[color:var(--ui-text)] disabled:opacity-40"
                >
                  <Upload size={13} />
                  {pushing ? "Pushing..." : "Push"}
                  {syncStatus && syncStatus.ahead > 0 && (
                    <span className="ml-auto text-[10px] text-[color:var(--ui-text-dim)] opacity-60">
                      {syncStatus.ahead}↑
                    </span>
                  )}
                </button>
                <button
                  onClick={handlePull}
                  disabled={pulling}
                  className="w-full px-3 py-2 text-left text-xs hover:bg-[var(--ui-panel-2)] transition-colors flex items-center gap-2 text-[color:var(--ui-text)] disabled:opacity-40"
                >
                  <Download size={13} />
                  {pulling ? "Pulling..." : "Pull"}
                  {syncStatus && syncStatus.behind > 0 && (
                    <span className="ml-auto text-[10px] text-[color:var(--ui-text-dim)] opacity-60">
                      {syncStatus.behind}↓
                    </span>
                  )}
                </button>
                <div className="border-t border-[var(--ui-border)] my-1" />
                <button
                  onClick={handleCreatePR}
                  className="w-full px-3 py-2 text-left text-xs hover:bg-[var(--ui-panel-2)] transition-colors flex items-center gap-2 text-[color:var(--ui-text)]"
                >
                  <GitPullRequest size={13} />
                  Create Pull Request
                </button>
                <button
                  onClick={() => {
                    setCreateBranchOpen(true);
                    setGitActionsOpen(false);
                  }}
                  className="w-full px-3 py-2 text-left text-xs hover:bg-[var(--ui-panel-2)] transition-colors flex items-center gap-2 text-[color:var(--ui-text)]"
                >
                  <GitBranch size={13} />
                  Create Branch
                </button>
              </div>
            )}
          </div>
          <button
            onClick={(e) => {
              e.stopPropagation();
              refresh();
            }}
            className="text-[color:var(--ui-text-dim)] hover:text-[color:var(--ui-text)] transition-colors p-1"
            title="Refresh"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {!rootPath && (
          <div className="flex items-center justify-center h-full text-[color:var(--ui-text-dim)] text-xs">
            No project open
          </div>
        )}

        {rootPath && totalChanges === 0 && !loading && (
          <div className="flex items-center justify-center h-full text-[color:var(--ui-text-dim)] text-xs">
            No changes
          </div>
        )}

        {rootPath && totalChanges > 0 && (
          <>
            <div className="flex flex-1 min-h-0 overflow-hidden">
              {fileExplorerOpen && (
                <div
                  className="relative shrink-0 border-r border-[var(--ui-border)] bg-[var(--ui-bg)]"
                  style={{ width: `${fileExplorerWidth}px` }}
                >
                  <div className="h-full overflow-y-auto">
                    <div className="px-3 py-2 text-[10px] uppercase tracking-[0.18em] text-[color:var(--ui-text-dim)]">
                      Files
                    </div>
                    <FileTree
                      nodes={fileTree}
                      selectedPath={effectiveSelectedDiffPath}
                      onSelect={focusDiff}
                    />
                  </div>
                  <div
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="Resize changed files panel"
                    onMouseDown={handleResizeStart}
                    className="absolute right-0 top-0 h-full w-2 translate-x-1/2 cursor-col-resize"
                  />
                </div>
              )}

              {/* Diff view - main scrollable area */}
              <div className="flex-1 min-w-0 min-h-0 overflow-y-auto">
                {diffCards.length === 0 && !loading && (
                  <div className="flex items-center justify-center h-32 text-[color:var(--ui-text-dim)] text-xs">
                    Click refresh to load diffs
                  </div>
                )}
                <div className="p-3 space-y-2">
                  {diffCards.map(
                    ({
                      key,
                      displayPath,
                      oldFile,
                      newFile,
                      additions,
                      deletions,
                    }) => (
                      <DiffCard
                        key={key}
                        cardRef={(node) => {
                          diffCardRefs.current[newFile.name] = node;
                        }}
                        selected={effectiveSelectedDiffPath === newFile.name}
                        onSelect={() => focusDiff(oldFile.name)}
                        displayPath={displayPath}
                        oldFile={oldFile}
                        newFile={newFile}
                        additions={additions}
                        deletions={deletions}
                        defaultExpanded={true}
                      />
                    )
                  )}
                </div>
              </div>
            </div>

            {/* Floating action bar */}
            <div className="shrink-0 flex items-center justify-center gap-2 px-3 py-2 border-t border-[var(--ui-border)] bg-[var(--ui-panel)]">
              {totalUnstaged > 0 && (
                <>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDiscardAll();
                    }}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-colors"
                  >
                    <Undo2 size={12} />
                    Revert All
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleStageAll();
                    }}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-green-500/30 text-green-400 hover:bg-green-500/10 transition-colors"
                  >
                    <Plus size={12} />
                    Stage All
                  </button>
                </>
              )}
              {totalUnstaged === 0 && stagedEntries.length > 0 && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleUnstageAll();
                  }}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-yellow-500/30 text-yellow-400 hover:bg-yellow-500/10 transition-colors"
                >
                  <Minus size={12} />
                  Unstage All
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {/* Status toast */}
      {statusMsg && (
        <div
          className={`absolute bottom-14 left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-md text-xs z-30 shadow-lg ${statusMsg.error
            ? "bg-red-500/20 text-red-300 border border-red-500/30"
            : "bg-green-500/20 text-green-300 border border-green-500/30"
            }`}
        >
          {statusMsg.text}
        </div>
      )}

      {/* Commit dialog */}
      {commitDialogOpen && (
        <div
          className="absolute inset-0 z-40 flex items-center justify-center bg-black/50"
          onClick={(e) => {
            e.stopPropagation();
            setCommitDialogOpen(false);
          }}
        >
          <div
            className="w-96 bg-[var(--ui-panel)] border border-[var(--ui-border)] rounded-xl shadow-2xl overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--ui-border)]">
              <div className="flex items-center gap-2">
                <GitCommit size={14} className="text-[color:var(--ui-text-dim)]" />
                <span className="text-sm font-medium text-[color:var(--ui-text)]">
                  Commit Changes
                </span>
              </div>
              <button
                onClick={() => setCommitDialogOpen(false)}
                className="text-[color:var(--ui-text-dim)] hover:text-[color:var(--ui-text)] transition-colors p-1"
              >
                <X size={14} />
              </button>
            </div>
            <div className="p-4 space-y-3">
              {stagedEntries.length === 0 && (
                <div className="text-xs text-yellow-400 bg-yellow-500/10 border border-yellow-500/20 rounded-md px-3 py-2">
                  No staged changes. Stage files before committing.
                </div>
              )}
              <div className="text-xs text-[color:var(--ui-text-dim)]">
                {stagedEntries.length} file
                {stagedEntries.length !== 1 ? "s" : ""} staged
              </div>
              <textarea
                value={commitMessage}
                onChange={(e) => setCommitMessage(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    handleCommit();
                  }
                }}
                placeholder="Commit message..."
                rows={4}
                autoFocus
                className="w-full bg-[var(--ui-bg)] text-[color:var(--ui-text)] text-xs rounded-md px-3 py-2 resize-none placeholder:text-[color:var(--ui-text-dim)] placeholder:opacity-40 border border-[var(--ui-border)] focus:border-[#5a9bc7] focus:outline-none"
              />
              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={() => setCommitDialogOpen(false)}
                  className="px-3 py-1.5 text-xs rounded-md text-[color:var(--ui-text-dim)] hover:text-[color:var(--ui-text)] hover:bg-[var(--ui-panel-2)] transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCommit}
                  disabled={
                    committing ||
                    !commitMessage.trim() ||
                    stagedEntries.length === 0
                  }
                  className="px-4 py-1.5 text-xs rounded-md bg-[#5a9bc7] hover:bg-[#4a8ab6] text-white font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {committing ? "Committing..." : "Commit"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Create branch dialog */}
      {createBranchOpen && (
        <div
          className="absolute inset-0 z-40 flex items-center justify-center bg-black/50"
          onClick={(e) => {
            e.stopPropagation();
            setCreateBranchOpen(false);
          }}
        >
          <div
            className="w-80 bg-[var(--ui-panel)] border border-[var(--ui-border)] rounded-xl shadow-2xl overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--ui-border)]">
              <div className="flex items-center gap-2">
                <GitBranch
                  size={14}
                  className="text-[color:var(--ui-text-dim)]"
                />
                <span className="text-sm font-medium text-[color:var(--ui-text)]">
                  Create Branch
                </span>
              </div>
              <button
                onClick={() => setCreateBranchOpen(false)}
                className="text-[color:var(--ui-text-dim)] hover:text-[color:var(--ui-text)] transition-colors p-1"
              >
                <X size={14} />
              </button>
            </div>
            <div className="p-4 space-y-3">
              <input
                value={newBranchName}
                onChange={(e) => setNewBranchName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleCreateBranch();
                  }
                }}
                placeholder="Branch name..."
                autoFocus
                className="w-full bg-[var(--ui-bg)] text-[color:var(--ui-text)] text-xs rounded-md px-3 py-2 placeholder:text-[color:var(--ui-text-dim)] placeholder:opacity-40 border border-[var(--ui-border)] focus:border-[#5a9bc7] focus:outline-none"
              />
              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={() => setCreateBranchOpen(false)}
                  className="px-3 py-1.5 text-xs rounded-md text-[color:var(--ui-text-dim)] hover:text-[color:var(--ui-text)] hover:bg-[var(--ui-panel-2)] transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreateBranch}
                  disabled={!newBranchName.trim()}
                  className="px-4 py-1.5 text-xs rounded-md bg-[#5a9bc7] hover:bg-[#4a8ab6] text-white font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  Create
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});

export const SourceControlTile = SourceControlPanel;

// ─── DiffCard ────────────────────────────────────────────────────────────────

const DiffCard = memo(function DiffCard({
  cardRef,
  selected,
  onSelect,
  displayPath,
  oldFile,
  newFile,
  additions,
  deletions,
  defaultExpanded,
}: {
  cardRef?: (node: HTMLDivElement | null) => void;
  selected?: boolean;
  onSelect?: () => void;
  displayPath: string;
  oldFile: FileContents;
  newFile: FileContents;
  additions: number;
  deletions: number;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const diffOptions = useDiffOptions();

  return (
    <div
      ref={cardRef}
      className={`rounded-lg bg-[var(--ui-accent-bubble)] overflow-hidden border ${selected
        ? "border-[color:var(--ui-accent)]"
        : "border-[var(--ui-border-subtle)]"
        }`}
    >
      <button
        type="button"
        onClick={() => {
          onSelect?.();
          setExpanded(!expanded);
        }}
        className="w-full px-3 py-2 text-left flex items-center gap-2 hover:bg-[var(--ui-panel-2)] transition-colors"
      >
        <span
          className="text-[color:var(--ui-text-dim)] text-xs shrink-0 transition-transform"
          style={{
            transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
          }}
        >
          ▾
        </span>
        <span className="text-[13px] text-[color:var(--ui-accent)] truncate flex-1 min-w-0">
          {displayPath}
        </span>
        <span className="shrink-0 text-xs flex items-center gap-2">
          <span className="text-green-400">+{additions}</span>
          <span className="text-red-400">-{deletions}</span>
        </span>
      </button>

      {expanded && (
        <div className="border-t border-[var(--ui-border)]">
          <MultiFileDiff oldFile={oldFile} newFile={newFile} options={diffOptions} />
        </div>
      )}
    </div>
  );
});

function buildFileTree(fileDiffs: FileDiff[], projectPath?: string): FileTreeNode[] {
  const root: FileTreeNode[] = [];

  for (const diff of fileDiffs) {
    const displayPath = stripProjectPath(diff.path, projectPath);
    const parts = displayPath.split("/").filter(Boolean);
    insertTreeNode(root, parts, diff, "");
  }

  const sortNodes = (nodes: FileTreeNode[]): FileTreeNode[] =>
    [...nodes]
      .map((node) => ({
        ...node,
        children: sortNodes(node.children),
      }))
      .sort((a, b) => {
        const aIsDir = a.children.length > 0 && !a.diff;
        const bIsDir = b.children.length > 0 && !b.diff;
        if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
        return a.name.localeCompare(b.name);
      });

  return sortNodes(root);
}

function insertTreeNode(
  nodes: FileTreeNode[],
  parts: string[],
  diff: FileDiff,
  parentPath: string
) {
  const [part, ...rest] = parts;
  if (!part) return;
  const path = parentPath ? `${parentPath}/${part}` : part;
  let node = nodes.find((candidate) => candidate.name === part);
  if (!node) {
    node = { name: part, path, children: [] };
    nodes.push(node);
  }
  if (rest.length === 0) {
    node.diff = diff;
    return;
  }
  insertTreeNode(node.children, rest, diff, path);
}

function FileTree({
  nodes,
  selectedPath,
  onSelect,
}: {
  nodes: FileTreeNode[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    if (!selectedPath) return;
    const ancestors = getAncestorPaths(selectedPath);
    if (ancestors.length === 0) return;
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const ancestor of ancestors) {
        if (!next.has(ancestor)) {
          next.add(ancestor);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [selectedPath]);

  const toggleDirectory = useCallback((path: string) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  return (
    <div className="pb-2">
      {nodes.map((node) => (
        <TreeNode
          key={node.path}
          node={node}
          depth={0}
          expandedPaths={expandedPaths}
          selectedPath={selectedPath}
          onSelect={onSelect}
          onToggleDirectory={toggleDirectory}
        />
      ))}
    </div>
  );
}

const TreeNode = memo(function TreeNode({
  node,
  depth,
  expandedPaths,
  selectedPath,
  onSelect,
  onToggleDirectory,
}: {
  node: FileTreeNode;
  depth: number;
  expandedPaths: Set<string>;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  onToggleDirectory: (path: string) => void;
}) {
  const isDirectory = node.children.length > 0 && !node.diff;
  const expanded = expandedPaths.has(node.path);
  const status = node.diff?.entry.status;
  const isSelected = selectedPath === node.path;

  return (
    <div>
      <button
        type="button"
        onClick={() => {
          if (isDirectory) {
            onToggleDirectory(node.path);
            return;
          }
          onSelect(node.path);
        }}
        className={`flex w-full items-center gap-2 pr-3 py-1.5 text-xs text-left transition-colors ${isSelected
          ? "bg-[var(--ui-panel-2)] text-[color:var(--ui-text)]"
          : "text-[color:var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)] hover:text-[color:var(--ui-text)]"
          }`}
        style={{ paddingLeft: `${12 + depth * 16}px` }}
        title={node.path}
      >
        <span className="w-3 shrink-0 text-[color:var(--ui-text-dim)]">
          {isDirectory ? (
            expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />
          ) : null}
        </span>
        {isDirectory ? (
          expanded ? (
            <FolderOpen size={13} className="shrink-0" />
          ) : (
            <Folder size={13} className="shrink-0" />
          )
        ) : (
          <FileText size={13} className="shrink-0" />
        )}
        <span className="truncate flex-1 min-w-0">{node.name}</span>
        {!isDirectory && status && (
          <span
            className={`shrink-0 w-4 text-center font-medium ${STATUS_COLORS[status] ?? "text-[color:var(--ui-text-dim)]"}`}
          >
            {STATUS_LABELS[status] ?? "?"}
          </span>
        )}
      </button>
      {isDirectory && expanded && (
        <div>
          {node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              expandedPaths={expandedPaths}
              selectedPath={selectedPath}
              onSelect={onSelect}
              onToggleDirectory={onToggleDirectory}
            />
          ))}
        </div>
      )}
    </div>
  );
});

function getAncestorPaths(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  const ancestors: string[] = [];
  let current = "";
  for (const part of parts.slice(0, -1)) {
    current = current ? `${current}/${part}` : part;
    ancestors.push(current);
  }
  return ancestors;
}
