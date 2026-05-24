// @ts-nocheck — ported from open-swe-app (Electron); strict checks applied when wiring cloud APIs.
import { useRef, useEffect, useLayoutEffect, useCallback, memo, useMemo, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { diffLines } from "diff";
import { MultiFileDiff } from "@pierre/diffs/react";
import { diffOptions } from "@/components/agents/utils/diffUtils";
import { CodeBlock } from "./CodeBlock";
import { Markdown } from "./Markdown";
import { ToolExecution } from "./ToolExecution";
import { ShellCommand } from "./ShellCommand";
import type {
  Chunk,
  Message,
  ToolExecutionChunk,
  Project,
  DiffData,
} from "@/lib/agents/types";

type RenderItem =
  | { type: "text-chunk"; key: string; chunk: Chunk }
  | { type: "explored-group"; key: string; id: string; chunks: ToolExecutionChunk[] }
  | { type: "edit-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "shell-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "tool-item"; key: string; chunk: ToolExecutionChunk };

function getChunkRenderKey(chunk: Chunk, sourceIndex: number): string {
  switch (chunk.kind) {
    case "tool-execution":
      return `tool-${chunk.toolCallId}`;
    case "text":
      return `text-${sourceIndex}`;
    case "code":
      return `code-${sourceIndex}`;
    case "error":
      return `error-${sourceIndex}`;
    case "list":
      return `list-${sourceIndex}`;
    case "image":
      return `image-${sourceIndex}`;
    default:
      return `chunk-${sourceIndex}`;
  }
}

function isEditTool(chunk: ToolExecutionChunk): boolean {
  const kind = chunk.toolKind;
  if (kind === "edit" || kind === "delete" || kind === "move") return true;
  if (chunk.diffs?.length) return true;
  if (chunk.diffData) return true;
  return false;
}

function isExplorationTool(chunk: ToolExecutionChunk): boolean {
  if (chunk.diffs?.length) return false;
  if (chunk.diffData) return false;
  const kind = chunk.toolKind;
  return kind === "read" || kind === "search";
}

function isShellTool(chunk: ToolExecutionChunk): boolean {
  return chunk.toolKind === "execute";
}

function buildRenderItems(chunks: Chunk[]): RenderItem[] {
  const items: RenderItem[] = [];
  let exploredBuffer: ToolExecutionChunk[] = [];
  let exploredStartIndex = -1;

  const flushExplored = () => {
    if (exploredBuffer.length === 0) return;
    const firstId = exploredBuffer[0]?.toolCallId;
    const id = `explored-${firstId || exploredStartIndex}`;
    items.push({
      type: "explored-group",
      key: id,
      id,
      chunks: [...exploredBuffer],
    });
    exploredBuffer = [];
    exploredStartIndex = -1;
  };

  for (let i = 0; i < chunks.length; i += 1) {
    const chunk = chunks[i];

    if (chunk.kind === "tool-execution") {
      if (isExplorationTool(chunk)) {
        if (exploredBuffer.length === 0) exploredStartIndex = i;
        exploredBuffer.push(chunk);
        continue;
      }

      flushExplored();

      if (isEditTool(chunk)) {
        items.push({ type: "edit-item", key: `tool-${chunk.toolCallId}`, chunk });
      } else if (isShellTool(chunk)) {
        items.push({ type: "shell-item", key: `tool-${chunk.toolCallId}`, chunk });
      } else {
        items.push({ type: "tool-item", key: `tool-${chunk.toolCallId}`, chunk });
      }
      continue;
    }

    if (chunk.kind === "text" && !chunk.text.trim()) continue;

    flushExplored();
    items.push({
      type: "text-chunk",
      key: getChunkRenderKey(chunk, i),
      chunk,
    });
  }

  flushExplored();
  return items;
}

function summarizeExploration(chunks: ToolExecutionChunk[]): string {
  const count = chunks.length;
  return `Explored ${count} file${count === 1 ? "" : "s"}`;
}

export interface ChangedFileSummaryItem {
  filePath: string;
  additions: number;
  deletions: number;
  originalContent: string;
  modifiedContent: string;
}

function countLines(text: string): number {
  if (text.length === 0) return 0;
  const segments = text.split("\n");
  return text.endsWith("\n") ? segments.length - 1 : segments.length;
}

function countLineChanges(originalContent: string | null, newContent: string): { additions: number; deletions: number } {
  const before = originalContent ?? "";
  const parts = diffLines(before, newContent, {
    ignoreWhitespace: false,
    newlineIsToken: false,
  });

  let additions = 0;
  let deletions = 0;

  for (const part of parts) {
    const lineCount = countLines(part.value);
    if (part.added) additions += lineCount;
    else if (part.removed) deletions += lineCount;
  }

  return { additions, deletions };
}

function stripProjectPathForDisplay(path: string, projectPath?: string): string {
  if (!projectPath) return path;
  const normalizedPath = path.replace(/\\/g, "/");
  const normalizedProjectPath = projectPath.replace(/\\/g, "/").replace(/\/+$/, "");
  if (!normalizedPath.startsWith(`${normalizedProjectPath}/`)) return path;
  return normalizedPath.slice(normalizedProjectPath.length + 1);
}

const TurnChangedFilesCard = memo(function TurnChangedFilesCard({
  files,
  totals,
  projectPath,
}: {
  files: ChangedFileSummaryItem[];
  totals: { additions: number; deletions: number };
  projectPath?: string;
}) {
  const [expandedByPath, setExpandedByPath] = useState<Record<string, boolean>>({});

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
        {files.map((file) => {
          const displayPath = stripProjectPathForDisplay(file.filePath, projectPath);
          const open = !!expandedByPath[file.filePath];
          const oldFile = { name: displayPath, contents: file.originalContent };
          const newFile = { name: displayPath, contents: file.modifiedContent };
          return (
            <div key={file.filePath} className="border-b last:border-b-0 border-[var(--ui-border)]">
              <button
                type="button"
                onClick={() => toggleFile(file.filePath)}
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
                    <div className="max-h-[250px] overflow-auto">
                      <MultiFileDiff
                        oldFile={oldFile}
                        newFile={newFile}
                        options={diffOptions}
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
});

export function summarizeChangedFiles(chunks: Chunk[]): ChangedFileSummaryItem[] {
  const byFile = new Map<string, { filePath: string; originalContent: string | null; modifiedContent: string }>();

  for (const chunk of chunks) {
    if (chunk.kind !== "tool-execution") continue;
    if (chunk.status !== "completed") continue;
    const diffEntries = chunk.diffs?.length ? chunk.diffs : (chunk.diffData ? [chunk.diffData] : []);
    if (diffEntries.length === 0) continue;

    for (const diffData of diffEntries as DiffData[]) {
      const existing = byFile.get(diffData.filePath);

      if (!existing) {
        byFile.set(diffData.filePath, {
          filePath: diffData.filePath,
          originalContent: diffData.originalContent,
          modifiedContent: diffData.newContent,
        });
        continue;
      }

      byFile.set(diffData.filePath, {
        filePath: existing.filePath,
        originalContent: existing.originalContent,
        modifiedContent: diffData.newContent,
      });
    }
  }

  return [...byFile.values()]
    .map((file) => {
      const { additions, deletions } = countLineChanges(file.originalContent, file.modifiedContent);
      return {
        filePath: file.filePath,
        additions,
        deletions,
        originalContent: file.originalContent ?? "",
        modifiedContent: file.modifiedContent,
      };
    })
    .sort((a, b) => a.filePath.localeCompare(b.filePath));
}

interface ApprovalCallbacks {
  onApprove?: (approvalRequestId: string) => void;
  onReject?: (approvalRequestId: string) => void;
  onAutoApprove?: (approvalRequestId: string) => void;
  onOpenDiff?: (diffData: { filePath: string; originalContent: string; modifiedContent: string }) => void;
}

export type MessageViewScrollControl = {
  scrollToBottom: () => void;
};

interface MessageViewProps extends ApprovalCallbacks {
  messages: Message[];
  isStreaming: boolean;
  project?: Project | null;
  contentWidthClass?: string;
  /** Horizontal padding on centered content (scroll track stays edge-to-edge). */
  contentPaddingClass?: string;
  /** Extra scroll padding so content can scroll under a bottom overlay (e.g. floating prompt). */
  bottomInset?: number;
  /** When "external", parent renders the scroll button (e.g. above a floating prompt). */
  scrollButtonSlot?: "internal" | "external";
  onShowScrollToBottomChange?: (show: boolean) => void;
  scrollControlRef?: React.MutableRefObject<MessageViewScrollControl | null>;
}

const BUSY_TEXTS: { present: string; past: string }[] = [
  { present: "vibing...",               past: "Vibed" },
  { present: "noodling...",             past: "Noodled" },
  { present: "pondering...",            past: "Pondered" },
  { present: "thinking really hard...", past: "Thought really hard" },
  { present: "spinning up...",          past: "Spun up" },
  { present: "connecting the dots...",  past: "Connected the dots" },
  { present: "brewing ideas...",        past: "Brewed ideas" },
  { present: "cooking...",              past: "Cooked" },
  { present: "crunching...",            past: "Crunched" },
  { present: "scheming...",             past: "Schemed" },
  { present: "processing...",           past: "Processed" },
];

function formatElapsed(ms: number): string {
  const secs = Math.round(ms / 1000);
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

function ChunkRenderer({
  chunk,
  projectPath,
  ...callbacks
}: { chunk: Chunk; projectPath?: string } & ApprovalCallbacks) {
  switch (chunk.kind) {
    case "text":
      return (
        <div className="text-[color:var(--ui-text)]">
          <Markdown content={chunk.text} />
        </div>
      );
    case "code":
      return <CodeBlock text={chunk.text} language={chunk.language} />;
    case "error":
      return <span className="text-red-400">{chunk.text}</span>;
    case "list":
      return (
        <div className="text-gray-300 ml-2">
          {chunk.lines.map((line, i) => (
            <div key={i}>- {line}</div>
          ))}
        </div>
      );
    case "tool-execution":
      return (
        <ToolExecution
          chunk={chunk}
          projectPath={projectPath}
          onApprove={callbacks.onApprove}
          onReject={callbacks.onReject}
          onAutoApprove={callbacks.onAutoApprove}
          onOpenDiff={callbacks.onOpenDiff}
        />
      );
    case "image":
      return (
        <img
          src={`data:${chunk.mimeType};base64,${chunk.base64}`}
          alt={chunk.fileName || "image"}
          className="max-w-48 max-h-48 rounded border border-gray-600"
        />
      );
  }
}

function UserMessage({ message }: { message: Message }) {
  const text = message.chunks
    .filter((c) => c.kind === "text")
    .map((c) => (c as { kind: "text"; text: string }).text)
    .join("");

  const images = message.chunks.filter((c) => c.kind === "image");
  const textRef = useRef<HTMLDivElement>(null);
  const [scrolledFromTop, setScrolledFromTop] = useState(false);
  const [scrolledFromBottom, setScrolledFromBottom] = useState(false);

  const updateScrollIndicators = useCallback(() => {
    const el = textRef.current;
    if (!el) return;
    setScrolledFromTop(el.scrollTop > 0);
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1);
  }, []);

  useLayoutEffect(() => {
    updateScrollIndicators();
  }, [text, updateScrollIndicators]);

  const textEdgeShadows = [
    scrolledFromTop ? "inset 0 12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
    scrolledFromBottom ? "inset 0 -12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="flex justify-end my-4">
      <div className="max-w-[78%]">
        {images.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap justify-end">
            {images.map((img, i) => (
              img.kind === "image" && (
                <img
                  key={i}
                  src={`data:${img.mimeType};base64,${img.base64}`}
                  alt={img.fileName || "image"}
                  className="max-w-48 max-h-48 rounded border border-gray-600"
                />
              )
            ))}
          </div>
        )}
        {text && (
          <div className="inline-block max-w-full rounded-2xl bg-[var(--ui-accent-bubble)] overflow-hidden">
            <div
              ref={textRef}
              onScroll={updateScrollIndicators}
              className="max-h-[250px] overflow-auto px-3 py-1.5 text-[color:var(--ui-text)] text-[13px] whitespace-pre-wrap break-words"
              style={{ boxShadow: textEdgeShadows || "none" }}
            >
              {text}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function AgentMessage({
  message,
  isStreaming,
  projectPath,
  ...callbacks
}: {
  message: Message;
  isStreaming?: boolean;
  projectPath?: string;
} & ApprovalCallbacks) {
  const renderItems = useMemo(() => buildRenderItems(message.chunks), [message.chunks]);
  const changedFiles = useMemo(() => summarizeChangedFiles(message.chunks), [message.chunks]);
  const changedFilesTotals = useMemo(() => {
    let additions = 0;
    let deletions = 0;
    for (const item of changedFiles) {
      additions += item.additions;
      deletions += item.deletions;
    }
    return { additions, deletions };
  }, [changedFiles]);
  const changedFilesByPath = useMemo(() => {
    const byPath = new Map<string, ChangedFileSummaryItem>();
    for (const file of changedFiles) {
      byPath.set(file.filePath, file);
    }
    return byPath;
  }, [changedFiles]);

  const exploredGroupIds = useMemo(
    () =>
      renderItems
        .filter((item): item is Extract<RenderItem, { type: "explored-group" }> => item.type === "explored-group")
        .map((item) => item.id),
    [renderItems],
  );
  const hasExploredGroups = exploredGroupIds.length > 0;
  const [expandedExploredGroups, setExpandedExploredGroups] = useState<Record<string, boolean>>({});
  const wasExplorationLiveRef = useRef(false);

  useEffect(() => {
    const isLive = !!isStreaming;

    if (!hasExploredGroups) {
      setExpandedExploredGroups({});
      wasExplorationLiveRef.current = isLive;
      return;
    }

    if (isLive) {
      const next: Record<string, boolean> = {};
      for (const id of exploredGroupIds) {
        next[id] = true;
      }
      setExpandedExploredGroups(next);
      wasExplorationLiveRef.current = true;
      return;
    }

    const shouldAutoCollapse = wasExplorationLiveRef.current;
    setExpandedExploredGroups((prev) => {
      const next: Record<string, boolean> = {};
      for (const id of exploredGroupIds) {
        next[id] = shouldAutoCollapse ? false : (prev[id] ?? false);
      }

      const prevKeys = Object.keys(prev);
      const nextKeys = Object.keys(next);
      if (prevKeys.length !== nextKeys.length) return next;
      for (const key of nextKeys) {
        if (prev[key] !== next[key]) return next;
      }
      return prev;
    });
    wasExplorationLiveRef.current = false;
  }, [hasExploredGroups, isStreaming, exploredGroupIds, message.id]);

  return (
    <div className="my-2 min-w-0 space-y-2">
      {renderItems.map((item) => {
        switch (item.type) {
          case "explored-group": {
            const summary = summarizeExploration(item.chunks);
            const isExpanded = expandedExploredGroups[item.id] ?? false;
            return (
              <div key={item.key}>
                <button
                  type="button"
                  onClick={() =>
                    setExpandedExploredGroups((prev) => ({
                      ...prev,
                      [item.id]: !(prev[item.id] ?? false),
                    }))
                  }
                  className="w-full flex items-center justify-between py-1 text-left hover:opacity-90 transition-opacity"
                >
                  <span className="text-[color:var(--ui-text-muted)] text-[12px]">{summary}</span>
                  <span className="text-[color:var(--ui-text-dim)] text-xs">{isExpanded ? "Hide" : "Show"}</span>
                </button>
                {isExpanded && (
                  <div className="pt-1 pb-1 space-y-0.5">
                    {item.chunks.map((chunk, chunkIndex) => (
                      <div key={chunk.toolCallId || `explored-chunk-${item.id}-${chunkIndex}`} className="flex-1 min-w-0 text-gray-500">
                        <ToolExecution
                          chunk={chunk}
                          projectPath={projectPath}
                          onOpenDiff={callbacks.onOpenDiff}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          }

          case "edit-item": {
            const fullFileDiff = item.chunk.diffData
              ? changedFilesByPath.get(item.chunk.diffData.filePath)
              : undefined;

            return (
              <div key={item.key}>
                <ToolExecution
                  chunk={item.chunk}
                  projectPath={projectPath}
                  onApprove={callbacks.onApprove}
                  onReject={callbacks.onReject}
                  onAutoApprove={callbacks.onAutoApprove}
                  onOpenDiff={callbacks.onOpenDiff}
                  resolvedDiffData={fullFileDiff ? {
                    originalContent: fullFileDiff.originalContent,
                    modifiedContent: fullFileDiff.modifiedContent,
                  } : undefined}
                />
              </div>
            );
          }

          case "shell-item":
            return (
              <div key={item.key}>
                <ShellCommand
                  chunk={item.chunk}
                  projectPath={projectPath}
                />
              </div>
            );

          case "tool-item":
            return (
              <div key={item.key}>
                <ToolExecution
                  chunk={item.chunk}
                  projectPath={projectPath}
                  onApprove={callbacks.onApprove}
                  onReject={callbacks.onReject}
                  onAutoApprove={callbacks.onAutoApprove}
                  onOpenDiff={callbacks.onOpenDiff}
                />
              </div>
            );

          case "text-chunk":
            return (
              <div key={item.key} className="flex-1 min-w-0">
                <ChunkRenderer
                  chunk={item.chunk}
                  projectPath={projectPath}
                  {...callbacks}
                />
              </div>
            );
        }
      })}

      {changedFiles.length > 0 && !isStreaming && (
        <TurnChangedFilesCard
          files={changedFiles}
          totals={changedFilesTotals}
          projectPath={projectPath}
        />
      )}
    </div>
  );
}

const MessageBubble = memo(function MessageBubble({
  message,
  isStreaming,
  projectPath,
  ...callbacks
}: {
  message: Message;
  isStreaming?: boolean;
  projectPath?: string;
} & ApprovalCallbacks) {
  if (message.author === "user") {
    return <UserMessage message={message} />;
  }
  return (
    <AgentMessage
      message={message}
      isStreaming={isStreaming}
      projectPath={projectPath}
      {...callbacks}
    />
  );
});

function ThinkingSpinner({ isStreaming }: { isStreaming: boolean }) {
  const [textIdx, setTextIdx] = useState(0);
  const [done, setDone] = useState<{ past: string; elapsed: string } | null>(null);
  const startTimeRef = useRef(0);
  const textIdxRef = useRef(textIdx);
  const wasStreamingRef = useRef(false);
  textIdxRef.current = textIdx;

  useEffect(() => {
    if (isStreaming) {
      wasStreamingRef.current = true;
      startTimeRef.current = Date.now();
      setTextIdx(Math.floor(Math.random() * BUSY_TEXTS.length));
      setDone(null);
    } else if (wasStreamingRef.current) {
      setDone({
        past: BUSY_TEXTS[textIdxRef.current].past,
        elapsed: formatElapsed(Date.now() - startTimeRef.current),
      });
    }
  }, [isStreaming]);

  useEffect(() => {
    if (!isStreaming) return;
    const BUSY_TEXT_ROTATE_INTERVAL_MS = 12000;
    const id = setInterval(() => setTextIdx((i) => (i + 1) % BUSY_TEXTS.length), BUSY_TEXT_ROTATE_INTERVAL_MS);
    return () => clearInterval(id);
  }, [isStreaming]);

  if (!isStreaming && !done) return null;

  if (done) {
    return (
      <div className="my-2 flex items-center gap-2">
        <span className="font-sans text-xs text-[color:var(--ui-text-dim)] select-none">*</span>
        <span className="text-xs text-[color:var(--ui-text-dim)]">{done.past} for {done.elapsed}</span>
      </div>
    );
  }

  return (
    <div className="my-2 flex items-center gap-2">
      <span className="shimmer-text text-xs">{BUSY_TEXTS[textIdx].present}</span>
    </div>
  );
}

const BOTTOM_LOCK_THRESHOLD_PX = 24;

export const MessageView = memo(function MessageView({
  messages,
  isStreaming,
  project,
  contentWidthClass = "max-w-[42rem]",
  contentPaddingClass = "px-6",
  bottomInset = 0,
  scrollButtonSlot = "internal",
  onShowScrollToBottomChange,
  scrollControlRef,
  onApprove,
  onReject,
  onAutoApprove,
  onOpenDiff,
}: MessageViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const autoScrollEnabledRef = useRef(true);
  const lastManualScrollTopRef = useRef(0);
  const previousScrollTopRef = useRef(0);
  const pendingScrollFrameRef = useRef<number | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const clearScheduledScroll = useCallback(() => {
    if (pendingScrollFrameRef.current === null) return;
    window.cancelAnimationFrame(pendingScrollFrameRef.current);
    pendingScrollFrameRef.current = null;
  }, []);

  const isNearBottom = useCallback((el: HTMLDivElement) => {
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distanceFromBottom <= BOTTOM_LOCK_THRESHOLD_PX;
  }, []);

  const syncScrollButtonVisibility = useCallback((el: HTMLDivElement) => {
    setShowScrollToBottom(!isNearBottom(el));
  }, [isNearBottom]);

  const scrollToBottomNow = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;

    el.scrollTop = el.scrollHeight;
    const currentTop = el.scrollTop;
    lastManualScrollTopRef.current = currentTop;
    previousScrollTopRef.current = currentTop;
    syncScrollButtonVisibility(el);
  }, [syncScrollButtonVisibility]);

  const scheduleScrollToBottom = useCallback(() => {
    if (!autoScrollEnabledRef.current) return;

    clearScheduledScroll();
    pendingScrollFrameRef.current = window.requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null;
      if (!autoScrollEnabledRef.current) return;
      scrollToBottomNow();
    });
  }, [clearScheduledScroll, scrollToBottomNow]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const handleScroll = () => {
      const currentTop = el.scrollTop;
      const scrolledUp = currentTop < previousScrollTopRef.current - 1;
      const nearBottom = isNearBottom(el);

      if (scrolledUp) {
        autoScrollEnabledRef.current = false;
        clearScheduledScroll();
      } else if (nearBottom) {
        autoScrollEnabledRef.current = true;
      }

      syncScrollButtonVisibility(el);
      lastManualScrollTopRef.current = currentTop;
      previousScrollTopRef.current = currentTop;
    };

    scrollToBottomNow();
    autoScrollEnabledRef.current = true;

    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", handleScroll);
      clearScheduledScroll();
    };
  }, [clearScheduledScroll, isNearBottom, scrollToBottomNow, syncScrollButtonVisibility]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    if (autoScrollEnabledRef.current) {
      scheduleScrollToBottom();
      return;
    }

    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
    const targetTop = Math.min(lastManualScrollTopRef.current, maxTop);
    const jumpDistance = Math.abs(el.scrollTop - targetTop);

    if (jumpDistance > el.clientHeight * 0.5) {
      el.scrollTop = targetTop;
    }

    previousScrollTopRef.current = el.scrollTop;
    syncScrollButtonVisibility(el);
  }, [messages, isStreaming, scheduleScrollToBottom, syncScrollButtonVisibility]);

  useEffect(() => {
    const scroller = scrollRef.current;
    const content = contentRef.current;
    if (!scroller || !content || typeof ResizeObserver === "undefined") return;

    const resizeObserver = new ResizeObserver(() => {
      if (autoScrollEnabledRef.current) {
        scheduleScrollToBottom();
        return;
      }

      const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      if (lastManualScrollTopRef.current > maxTop) {
        scroller.scrollTop = maxTop;
        lastManualScrollTopRef.current = maxTop;
        previousScrollTopRef.current = maxTop;
      }

      syncScrollButtonVisibility(scroller);
    });

    resizeObserver.observe(scroller);
    resizeObserver.observe(content);

    return () => resizeObserver.disconnect();
  }, [scheduleScrollToBottom, syncScrollButtonVisibility]);

  const visibleMessages = useMemo(() => messages.filter((message) => !message.hidden), [messages]);

  const handleScrollToBottom = useCallback(() => {
    autoScrollEnabledRef.current = true;
    clearScheduledScroll();
    scrollToBottomNow();
  }, [clearScheduledScroll, scrollToBottomNow]);

  useEffect(() => {
    if (!scrollControlRef) return;
    scrollControlRef.current = { scrollToBottom: handleScrollToBottom };
    return () => {
      scrollControlRef.current = null;
    };
  }, [handleScrollToBottom, scrollControlRef]);

  useEffect(() => {
    onShowScrollToBottomChange?.(showScrollToBottom);
  }, [onShowScrollToBottomChange, showScrollToBottom]);

  return (
    <div className="relative flex-1 min-h-0 min-w-0">
      <div
        ref={scrollRef}
        className="h-full min-h-0 min-w-0 overflow-y-auto overflow-x-hidden py-5 text-[13px] leading-6 font-sans antialiased"
      >
        <div
          ref={contentRef}
          className={`w-full ${contentWidthClass} mx-auto min-w-0 ${contentPaddingClass}`}
          style={bottomInset > 0 ? { paddingBottom: bottomInset } : undefined}
        >
          {visibleMessages.map((message, index) => (
            <MessageBubble
              key={message.id}
              message={message}
              isStreaming={isStreaming && index === visibleMessages.length - 1}
              projectPath={project?.path}
              onApprove={onApprove}
              onReject={onReject}
              onAutoApprove={onAutoApprove}
              onOpenDiff={onOpenDiff}
            />
          ))}
          <ThinkingSpinner isStreaming={isStreaming} />
        </div>
      </div>

      {scrollButtonSlot === "internal" && showScrollToBottom && (
        <button
          type="button"
          onClick={handleScrollToBottom}
          aria-label="Scroll to bottom"
          className="absolute left-1/2 z-30 inline-flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full bg-[var(--ui-panel-2)] text-[color:var(--ui-text-muted)] shadow-md transition-colors hover:bg-[var(--ui-panel)] hover:text-[color:var(--ui-text)]"
          style={{ bottom: bottomInset > 0 ? bottomInset + 8 : 16 }}
        >
          <ChevronDown className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
});
