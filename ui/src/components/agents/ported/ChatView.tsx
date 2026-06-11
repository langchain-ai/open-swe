import { useCallback, useRef, useEffect, useState, forwardRef } from "react";
import { useStore } from "../../store";
import { useShallow } from 'zustand/react/shallow';
import { Messages, summarizeChangedFiles } from "@/components/agents/messages";
import { PromptBar } from "./PromptBar";
import { TodoList } from "./TodoList";
import { Logo } from "./Logo";
import { ThreadPicker } from "./ThreadPicker";
import { CompactingIndicator } from "./CompactingIndicator";
import { executeCommand } from "../../commands";
import type { Message, ChatMessage, ChatMessageContentBlock, ImageChunk, Thread, ToolExecutionChunk, ScheduledTask as ScheduledTaskType } from "@/lib/agents/types";

const PROMPT_CONTENT_WIDTH = "max-w-[44rem]";
const STACKED_STATUS_WIDTH = PROMPT_CONTENT_WIDTH;
const MESSAGE_CONTENT_WIDTH = "max-w-[42rem]";

function messagesToChatMessages(messages: Message[]): ChatMessage[] {
  const chatMessages: ChatMessage[] = [];
  for (const msg of messages) {
    if (msg.author === "user" || msg.author === "agent") {
      const role = msg.author === "user" ? "user" as const : "assistant" as const;
      const hasImages = msg.chunks.some((c) => c.kind === "image");

      if (hasImages) {
        const blocks: ChatMessageContentBlock[] = [];
        for (const c of msg.chunks) {
          if (c.kind === "image") {
            blocks.push({ type: "image_url", image_url: { url: `data:${c.mimeType};base64,${c.base64}` } });
          } else if (c.kind === "text") {
            blocks.push({ type: "text", text: c.text });
          } else if (c.kind === "code") {
            blocks.push({ type: "text", text: `\n\`\`\`${c.language || ""}\n${c.text}\n\`\`\`\n` });
          }
        }
        if (blocks.length > 0) chatMessages.push({ role, content: blocks });
      } else {
        const textContent = msg.chunks
          .map((c) => {
            if (c.kind === "text") return c.text;
            if (c.kind === "code") return `\n\`\`\`${c.language || ""}\n${c.text}\n\`\`\`\n`;
            return "";
          })
          .join("");
        if (textContent) chatMessages.push({ role, content: textContent });
      }
    }
  }
  return chatMessages;
}

function findLatestPendingApproval(messages: Message[]): ToolExecutionChunk | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    for (let j = messages[i].chunks.length - 1; j >= 0; j -= 1) {
      const chunk = messages[i].chunks[j];
      if (chunk.kind === "tool-execution" && chunk.status === "pending" && chunk.approvalRequestId) {
        return chunk;
      }
    }
  }
  return null;
}

interface ChatViewProps {
  tabId: string;
}

export function ChatView({ tabId }: ChatViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const dragDepthRef = useRef(0);
  const [pendingImages, setPendingImages] = useState<ImageChunk[]>([]);
  const [queuedSubmissions, setQueuedSubmissions] = useState<Array<{ query: string; images: ImageChunk[] }>>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [showThreadPicker, setShowThreadPicker] = useState(false);
  const [showLoopManager, setShowLoopManager] = useState(false);
  const [loopTasks, setLoopTasks] = useState<ScheduledTaskType[]>([]);
  const loopManagerRef = useRef<HTMLDivElement>(null);

  const tab = useStore(state => state.tabs[tabId] ?? null);
  const session = useStore(state => {
    const t = state.tabs[tabId];
    return t ? state.sessions[t.sessionId] ?? null : null;
  });
  const hasScheduledPrompt = useStore(state => {
    const t = state.tabs[tabId];
    if (!t) return false;
    return state.scheduledPrompts.some(p => p.sessionId === t.sessionId);
  });
  const loopTaskCount = useStore(state => {
    const t = state.tabs[tabId];
    if (!t) return 0;
    return state.sessionLoopCounts[t.sessionId] || 0;
  });
  const actions = useStore(useShallow(state => ({
    setTabProject: state.setTabProject,
    createSession: state.createSession,
    addMessageToSession: state.addMessageToSession,
    startStreaming: state.startStreaming,
    setAutoApproveSession: state.setAutoApproveSession,
    resumeThread: state.resumeThread,
    consumeScheduledPrompt: state.consumeScheduledPrompt,
    setRightPanelTab: state.setRightPanelTab,
  })));

  useEffect(() => {
    if (session) window.agent.setMode(session.id, session.mode);
  }, [session?.id, session?.mode]);

  useEffect(() => {
    setQueuedSubmissions([]);
  }, [session?.id]);

  useEffect(() => {
    const project = tab?.project;
    if (!project?.path) return;
    if (project.worktreeType === 'worktree') return;

    const sync = () => { window.git.syncLocalBranch(project.path); };
    sync();
    const interval = setInterval(sync, 3000);
    return () => clearInterval(interval);
  }, [tab?.project?.path, tab?.project?.worktreeType]);

  const handleApprove = useCallback((id: string) => {
    window.agent.respondToApproval({ requestId: id, decision: "approve" });
  }, []);

  const handleReject = useCallback((id: string) => {
    window.agent.respondToApproval({ requestId: id, decision: "reject" });
  }, []);

  const handleAutoApprove = useCallback((id: string) => {
    if (session) actions.setAutoApproveSession(session.id, true);
    window.agent.respondToApproval({ requestId: id, decision: "auto-approve" });
  }, [session, actions]);

  const hasFileDragData = (e: React.DragEvent) => e.dataTransfer.types.includes("Files");

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    if (!hasFileDragData(e)) return;
    e.preventDefault(); e.stopPropagation();
    dragDepthRef.current += 1;
    setIsDragOver(true);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (!hasFileDragData(e)) return;
    e.preventDefault(); e.stopPropagation();
    if (!isDragOver) setIsDragOver(true);
  }, [isDragOver]);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (!hasFileDragData(e)) return;
    e.preventDefault(); e.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    if (!hasFileDragData(e)) return;
    e.preventDefault(); e.stopPropagation();
    dragDepthRef.current = 0;
    setIsDragOver(false);

    const files = Array.from(e.dataTransfer.files).filter(
      (f) => f.type === "image/png" || f.type === "image/jpeg"
    );
    for (const file of files) {
      const reader = new FileReader();
      reader.onload = () => {
        const base64 = (reader.result as string).split(",")[1];
        setPendingImages((prev) => [...prev, { kind: "image", base64, mimeType: file.type, fileName: file.name }]);
      };
      reader.readAsDataURL(file);
    }
  }, []);

  const handleRemoveImage = useCallback((index: number) => {
    setPendingImages((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleThreadSelect = useCallback((thread: Thread) => {
    setShowThreadPicker(false);
    actions.resumeThread(tabId, thread);
  }, [actions, tabId]);

  const handleOpenLoopManager = useCallback(async () => {
    if (!session?.id) return;
    const tasks = await window.cron.list(session.id);
    setLoopTasks(tasks);
    setShowLoopManager(true);
  }, [session?.id]);

  const handleDeleteLoop = useCallback(async (taskId: string) => {
    await window.cron.delete(taskId);
    setLoopTasks(prev => prev.filter(t => t.id !== taskId));
  }, []);

  const handleDeleteAllLoops = useCallback(async () => {
    for (const task of loopTasks) await window.cron.delete(task.id);
    setLoopTasks([]);
    setShowLoopManager(false);
  }, [loopTasks]);

  const handleClickOutsideLoopManager = useCallback((e: MouseEvent) => {
    if (loopManagerRef.current && !loopManagerRef.current.contains(e.target as Node)) {
      setShowLoopManager(false);
    }
  }, []);

  useEffect(() => {
    if (showLoopManager) {
      document.addEventListener('mousedown', handleClickOutsideLoopManager);
      return () => document.removeEventListener('mousedown', handleClickOutsideLoopManager);
    }
  }, [showLoopManager, handleClickOutsideLoopManager]);

  useEffect(() => {
    if (loopTaskCount === 0) setShowLoopManager(false);
  }, [loopTaskCount]);

  const handleOpenDiff = useCallback(
    (_diffData: { filePath: string; originalContent: string; modifiedContent: string }) => {
      // TODO: pass diffData to the right panel so it can show the specific file diff
      actions.setRightPanelTab('source-control');
    },
    [actions],
  );

  const handleContainerClick = useCallback(() => {
    const textarea = containerRef.current?.querySelector("textarea");
    if (textarea) textarea.focus();
  }, []);

  const dragProps = {
    onDragEnter: handleDragEnter,
    onDragOver: handleDragOver,
    onDragLeave: handleDragLeave,
    onDrop: handleDrop,
  };

  const startAgentRun = useCallback((sessionId: string, query: string, images: ImageChunk[], displayText?: string) => {
    const freshSession = useStore.getState().sessions[sessionId];
    if (!freshSession) return;

    const displayQuery = displayText ?? query;
    const chunks = [...images, { kind: "text" as const, text: displayQuery }];
    const chatHistory = messagesToChatMessages(freshSession.messages);

    if (images.length > 0) {
      const blocks: ChatMessageContentBlock[] = [
        ...images.map((img) => ({ type: "image_url" as const, image_url: { url: `data:${img.mimeType};base64,${img.base64}` } })),
        { type: "text" as const, text: query },
      ];
      chatHistory.push({ role: "user", content: blocks });
    } else {
      chatHistory.push({ role: "user", content: query });
    }

    actions.addMessageToSession(sessionId, "user", chunks);
    actions.startStreaming(sessionId);
    window.agent.stream(sessionId, tabId, chatHistory, freshSession.modelConfig, freshSession.mode);
  }, [actions, tabId]);

  useEffect(() => {
    if (!session) return;
    if (session.isCompacting || session.isStreaming || session.busy) return;
    if (queuedSubmissions.length === 0) return;

    const next = queuedSubmissions[0];
    setQueuedSubmissions((prev) => prev.slice(1));
    startAgentRun(session.id, next.query, next.images);
  }, [session, queuedSubmissions, startAgentRun]);

  useEffect(() => {
    if (!session) return;
    if (session.isCompacting || session.isStreaming || session.busy) return;
    if (!hasScheduledPrompt) return;

    const scheduledPrompt = actions.consumeScheduledPrompt(session.id);
    if (!scheduledPrompt) return;
    startAgentRun(session.id, scheduledPrompt.prompt, []);
  }, [session, hasScheduledPrompt, actions, startAgentRun]);

  const handleSubmit = useCallback(
    async (query: string) => {
      if (!tab) return;

      if (query.startsWith("/")) {
        const commandExecuted = executeCommand(query, {
          sessionId: session?.id || null,
          createSession: () => actions.createSession(tabId),
          addSystemMessage: (sessionId, chunks) => actions.addMessageToSession(sessionId, 'system', chunks),
        });
        if (commandExecuted) return;
      }

      if (!session) return;
      if (session.isCompacting) return;

      const images = pendingImages;
      setPendingImages([]);

      if (session.isStreaming || session.busy) {
        setQueuedSubmissions((prev) => [...prev, { query, images }]);
        window.agent.cancel(session.id);
        return;
      }

      startAgentRun(session.id, query, images);
    },
    [tab, session, tabId, pendingImages, startAgentRun, actions],
  );

  if (!tab || !tab.project) return null;

  const hasMessages = session && session.messages.length > 0;
  const streamingMessageId = session?.streamingMessageId ?? null;
  const streamingMessage = streamingMessageId
    ? session?.messages.find((m) => m.id === streamingMessageId) ?? null
    : null;
  const streamingChangedFiles = streamingMessage ? summarizeChangedFiles(streamingMessage.chunks) : [];
  let streamingAdditions = 0;
  let streamingDeletions = 0;
  for (const file of streamingChangedFiles) {
    streamingAdditions += file.additions;
    streamingDeletions += file.deletions;
  }
  const todos = session?.todos ?? [];
  const hasTodos = todos.length > 0;
  const hasStreamingChangedFiles = !!session?.isStreaming && streamingChangedFiles.length > 0;
  const hasActiveLoops = loopTaskCount > 0;
  const hasConnectedStack = hasTodos || hasStreamingChangedFiles || hasActiveLoops;
  const runActive = !!session?.isStreaming || !!session?.busy;
  const pendingApproval = session ? findLatestPendingApproval(session.messages) : null;

  if (!hasMessages) {
    return (
      <div
        ref={containerRef}
        className="relative flex flex-col h-full bg-[var(--ui-bg)] text-[color:var(--ui-text)]"
        onClick={handleContainerClick}
        {...dragProps}
      >
        <div className="flex-1 flex flex-col items-center justify-center px-4">
          <div className={`w-full ${PROMPT_CONTENT_WIDTH} flex flex-col items-center gap-6 min-w-0`}>
            <Logo />
            <div className={`w-full ${PROMPT_CONTENT_WIDTH} min-w-0`}>
              {hasActiveLoops && (
                <LoopManagerBlock
                  ref={loopManagerRef}
                  loopTaskCount={loopTaskCount}
                  runActive={runActive}
                  showLoopManager={showLoopManager}
                  loopTasks={loopTasks}
                  onOpenLoopManager={handleOpenLoopManager}
                  onDeleteLoop={handleDeleteLoop}
                  onDeleteAllLoops={handleDeleteAllLoops}
                  onCloseLoopManager={() => setShowLoopManager(false)}
                  className="-mb-2"
                />
              )}
              <PromptBar
                onSubmit={handleSubmit}
                busy={session?.busy ?? false}
                projectPath={tab.project.worktreePath || tab.project.path}
                mainProjectPath={tab.project.path}
                gitBranch={tab.project.gitBranch}
                githubPR={tab.project.githubPR}
                sessionId={tab.sessionId}
                tabId={tabId}
                isFocused={true}
                isDragOver={isDragOver}
                pendingImages={pendingImages}
                onRemoveImage={handleRemoveImage}
                dropUp={false}
                worktreeType={tab.project.worktreeType}
                worktreePath={tab.project.worktreePath}
                connectedTop={hasActiveLoops}
                pendingApproval={pendingApproval ? {
                  requestId: pendingApproval.approvalRequestId!,
                  title: pendingApproval.title,
                  toolKind: pendingApproval.toolKind,
                  input: pendingApproval.input ?? {},
                  diffData: pendingApproval.diffData,
                } : null}
                onApproveApproval={handleApprove}
                onRejectApproval={handleReject}
                onAutoApproveApproval={handleAutoApprove}
              />
            </div>
          </div>
        </div>
        {showThreadPicker && tab.project && (
          <ThreadPicker
            projectId={tab.project.id}
            onSelect={handleThreadSelect}
            onClose={() => setShowThreadPicker(false)}
          />
        )}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="relative flex flex-col h-full bg-[var(--ui-bg)] text-[color:var(--ui-text)] overflow-hidden"
      onClick={handleContainerClick}
      {...dragProps}
    >
      <Messages
        messages={session!.messages}
        isStreaming={session!.isStreaming}
        contentWidthClass={MESSAGE_CONTENT_WIDTH}
        onApprove={handleApprove}
        onReject={handleReject}
        onAutoApprove={handleAutoApprove}
        onOpenDiff={handleOpenDiff}
        project={tab.project}
      />
      {session!.isCompacting ? (
        <div className="px-4 py-4 shrink-0">
          <CompactingIndicator />
        </div>
      ) : (
        <>
          {hasConnectedStack && (
            <div className="px-4 shrink-0">
              <div className={`w-full ${STACKED_STATUS_WIDTH} mx-auto min-w-0 -mb-2`}>
                {hasActiveLoops && (
                  <LoopManagerBlock
                    ref={loopManagerRef}
                    loopTaskCount={loopTaskCount}
                    runActive={runActive}
                    showLoopManager={showLoopManager}
                    loopTasks={loopTasks}
                    onOpenLoopManager={handleOpenLoopManager}
                    onDeleteLoop={handleDeleteLoop}
                    onDeleteAllLoops={handleDeleteAllLoops}
                    onCloseLoopManager={() => setShowLoopManager(false)}
                  />
                )}
                {hasTodos && (
                  <TodoList
                    todos={todos}
                    runActive={runActive}
                    className={`${hasActiveLoops ? "rounded-t-none border-t-0" : "rounded-t-xl"} rounded-b-none ${!hasStreamingChangedFiles ? "pb-2" : ""}`}
                  />
                )}
                {hasStreamingChangedFiles && (
                  <div
                    className={`border border-[var(--ui-border)] bg-[var(--ui-code-bubble)] px-3 pt-2 pb-4 flex items-center justify-between gap-3 text-xs rounded-b-none ${(hasTodos || hasActiveLoops) ? "rounded-t-none border-t-0" : "rounded-t-xl"}`}
                  >
                    <span className="text-[color:var(--ui-text-muted)] truncate">
                      {streamingChangedFiles.length} file{streamingChangedFiles.length === 1 ? "" : "s"} changed
                      <span className="ml-2 text-green-400">+{streamingAdditions}</span>
                      <span className="ml-1 text-red-400">-{streamingDeletions}</span>
                    </span>
                    <button
                      type="button"
                      className="shrink-0 text-[color:var(--ui-accent)] hover:opacity-80 transition-opacity"
                      onClick={() => handleOpenDiff({
                        filePath: streamingChangedFiles[streamingChangedFiles.length - 1].filePath,
                        originalContent: streamingChangedFiles[streamingChangedFiles.length - 1].originalContent,
                        modifiedContent: streamingChangedFiles[streamingChangedFiles.length - 1].modifiedContent,
                      })}
                    >
                      Review changes
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
          <div className="px-4 pb-4 shrink-0">
            <div className={`w-full ${PROMPT_CONTENT_WIDTH} mx-auto min-w-0`}>
              <PromptBar
                onSubmit={handleSubmit}
                busy={session!.busy}
                projectPath={tab.project.worktreePath || tab.project.path}
                mainProjectPath={tab.project.path}
                gitBranch={tab.project.gitBranch}
                githubPR={tab.project.githubPR}
                sessionId={tab.sessionId}
                tabId={tabId}
                isFocused={true}
                isDragOver={isDragOver}
                pendingImages={pendingImages}
                onRemoveImage={handleRemoveImage}
                worktreeType={tab.project.worktreeType}
                worktreePath={tab.project.worktreePath}
                connectedTop={hasConnectedStack}
                pendingApproval={pendingApproval ? {
                  requestId: pendingApproval.approvalRequestId!,
                  title: pendingApproval.title,
                  toolKind: pendingApproval.toolKind,
                  input: pendingApproval.input ?? {},
                  diffData: pendingApproval.diffData,
                } : null}
                onApproveApproval={handleApprove}
                onRejectApproval={handleReject}
                onAutoApproveApproval={handleAutoApprove}
              />
            </div>
          </div>
        </>
      )}
      {showThreadPicker && tab.project && (
        <ThreadPicker
          projectId={tab.project.id}
          onSelect={handleThreadSelect}
          onClose={() => setShowThreadPicker(false)}
        />
      )}
    </div>
  );
}

interface LoopManagerBlockProps {
  loopTaskCount: number;
  runActive: boolean;
  showLoopManager: boolean;
  loopTasks: ScheduledTaskType[];
  onOpenLoopManager: () => void;
  onDeleteLoop: (taskId: string) => void;
  onDeleteAllLoops: () => void;
  onCloseLoopManager: () => void;
  className?: string;
}

const LoopManagerBlock = forwardRef<HTMLDivElement, LoopManagerBlockProps>(function LoopManagerBlock(
  { loopTaskCount, runActive, showLoopManager, loopTasks, onOpenLoopManager, onDeleteLoop, onDeleteAllLoops, onCloseLoopManager, className },
  ref,
) {
  return (
    <div ref={ref} className={`relative ${className || ''}`}>
      <button
        type="button"
        onClick={onOpenLoopManager}
        className="w-full border border-[var(--ui-border)] bg-[var(--ui-code-bubble)] px-3 pt-2 pb-4 flex items-center justify-between gap-3 text-xs rounded-t-xl rounded-b-none mb-0 hover:bg-[var(--ui-bg-hover)] transition-colors"
      >
        <span className="flex items-center gap-3">
          <span className="relative flex h-2 w-2">
            <span className={`${runActive ? "" : "animate-ping"} absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75`} />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-500" />
          </span>
          <span className="text-[color:var(--ui-text-muted)]">
            {loopTaskCount} active loop{loopTaskCount === 1 ? "" : "s"}{runActive ? " — running" : " monitoring"}
          </span>
        </span>
        <span className="text-[color:var(--ui-text-muted)]">Manage</span>
      </button>
      {showLoopManager && (
        <div className="absolute bottom-full left-0 right-0 mb-1 z-30">
          <div className="border border-[var(--ui-border)] bg-[var(--ui-bg)] rounded-xl shadow-lg overflow-hidden">
            <div className="px-3 py-2 border-b border-[var(--ui-border)] flex items-center justify-between">
              <span className="text-xs font-medium text-[color:var(--ui-text)]">Scheduled Loops</span>
              <div className="flex items-center gap-2">
                {loopTasks.length > 0 && (
                  <button type="button" onClick={onDeleteAllLoops} className="text-xs text-red-400 hover:text-red-300 transition-colors">
                    Cancel all
                  </button>
                )}
                <button type="button" onClick={onCloseLoopManager} className="text-[color:var(--ui-text-muted)] hover:text-[color:var(--ui-text)] transition-colors">
                  ✕
                </button>
              </div>
            </div>
            <div className="max-h-48 overflow-y-auto">
              {loopTasks.length === 0 ? (
                <div className="px-3 py-4 text-xs text-center text-[color:var(--ui-text-muted)]">No active loops</div>
              ) : (
                loopTasks.map(task => (
                  <div key={task.id} className="px-3 py-2 border-b border-[var(--ui-border)] last:border-b-0 flex items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-[color:var(--ui-text)] truncate">{task.prompt}</div>
                      <div className="text-xs text-[color:var(--ui-text-muted)] mt-0.5">
                        {task.isRecurring ? `Every ${Math.round(task.intervalMs / 60000)}m` : "One-time"}{" · "}<span className="font-mono">{task.id}</span>
                      </div>
                    </div>
                    <button type="button" onClick={() => onDeleteLoop(task.id)} className="shrink-0 text-xs text-red-400 hover:text-red-300 transition-colors p-1">✕</button>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
});
