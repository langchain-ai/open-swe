import { useState, useRef, useEffect, useLayoutEffect, useMemo, memo, useCallback } from 'react';
import { useStore } from '../../store';
import { useShallow } from 'zustand/react/shallow';
import { CommandAutocomplete, getFilteredCommandCount, getCommandAtIndex } from './CommandAutocomplete';
import {
  FileAutocomplete,
  buildFileSearchIndex,
  getFileAutocompleteContext,
  insertFileTag,
  searchFileSuggestions,
} from './FileAutocomplete';
import { getModelsForHarness, MODEL_DISPLAY_NAMES, type ModelOption } from '../../config/models';
import { ContextIndicator } from './ContextIndicator';
import { WorktreeSelector } from './WorktreeSelector';
import type { Command } from '../../commands';
import type { ApprovalDecision, DiffData, ImageChunk, ModelConfig, PermissionMode, WorktreeType, AcpToolKind } from '@/lib/agents/types';

const PERMISSION_OPTIONS: Array<{ value: PermissionMode; label: string }> = [
  { value: 'default', label: 'Default permissions' },
  { value: 'full', label: 'Full access' },
];

const PROMPT_TEXTAREA_MAX_HEIGHT = 200;

interface PromptApprovalRequest {
  requestId: string;
  title: string;
  toolKind: AcpToolKind;
  input: Record<string, unknown>;
  diffData?: DiffData;
}

interface ApprovalOption {
  label: string;
  decision: ApprovalDecision;
}

interface ApprovalPromptContent {
  question: string;
  preview: string;
  options: ApprovalOption[];
}

function truncateText(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

function getFilePathFromApproval(request: PromptApprovalRequest): string | null {
  if (request.diffData?.filePath) return request.diffData.filePath;
  if (typeof request.input.filePath === 'string') return request.input.filePath;
  if (typeof request.input.path === 'string') return request.input.path;
  return null;
}

function buildApprovalPromptContent(request: PromptApprovalRequest): ApprovalPromptContent {
  const { toolKind, title, input, diffData } = request;

  if (toolKind === 'execute') {
    const command = typeof input.command === 'string' ? input.command.trim() : '';
    const commandPreview = command || '(empty command)';
    return {
      question: 'Do you want to allow running this command?',
      preview: commandPreview,
      options: [
        { label: 'Yes', decision: 'approve' },
        {
          label: `Yes, and don't ask again for commands that start with ${truncateText(commandPreview, 64)}`,
          decision: 'auto-approve',
        },
        { label: 'No, and tell Open SWE what to do differently', decision: 'reject' },
      ],
    };
  }

  const targetPath = getFilePathFromApproval(request);
  const isFileUpdate = toolKind === 'edit' || toolKind === 'delete' || toolKind === 'move' || diffData != null;

  let question = 'Do you want to allow this action?';
  if (isFileUpdate) {
    if (toolKind === 'delete') {
      question = targetPath
        ? `Do you want to allow deleting ${targetPath}?`
        : 'Do you want to allow deleting this file?';
    } else if (toolKind === 'move') {
      question = targetPath
        ? `Do you want to allow moving ${targetPath}?`
        : 'Do you want to allow moving this file?';
    } else {
      question = targetPath
        ? `Do you want to allow updating ${targetPath}?`
        : 'Do you want to allow updating this file?';
    }
  }

  const preview = targetPath
    ? `${title} ${targetPath}`
    : `${title} ${truncateText(JSON.stringify(input), 180)}`;

  return {
    question,
    preview,
    options: [
      { label: 'Yes', decision: 'approve' },
      {
        label: isFileUpdate
          ? 'Yes, and allow all edits for this session'
          : 'Yes, and allow similar actions for this session',
        decision: 'auto-approve',
      },
      { label: 'No, and tell Open SWE what to do differently', decision: 'reject' },
    ],
  };
}

interface PromptBarProps {
  onSubmit: (query: string) => void;
  busy: boolean;
  projectPath?: string;
  /** The main repo root path (not the worktree path). Used for git operations. */
  mainProjectPath?: string;
  gitBranch?: string;
  githubPR?: { number: number; url: string } | null;
  sessionId: string;
  tabId: string;
  isFocused: boolean;
  isDragOver?: boolean;
  pendingImages?: ImageChunk[];
  onRemoveImage?: (index: number) => void;
  dropUp?: boolean;
  worktreeType?: WorktreeType;
  worktreePath?: string;
  connectedTop?: boolean;
  pendingApproval?: PromptApprovalRequest | null;
  onApproveApproval?: (approvalRequestId: string) => void;
  onRejectApproval?: (approvalRequestId: string) => void;
  onAutoApproveApproval?: (approvalRequestId: string) => void;
}

export const PromptBar = memo(function PromptBar({
  onSubmit,
  busy,
  projectPath,
  mainProjectPath,
  gitBranch,
  githubPR,
  sessionId,
  tabId,
  isFocused,
  isDragOver = false,
  pendingImages,
  onRemoveImage,
  dropUp = true,
  worktreeType,
  worktreePath,
  connectedTop = false,
  pendingApproval = null,
  onApproveApproval,
  onRejectApproval,
  onAutoApproveApproval,
}: PromptBarProps) {
  const {
    query,
    permissionMode,
    setPermissionMode,
    defaultModelConfig,
    sessionModelConfig,
    setSessionModelConfig,
    setSessionPromptDraft,
    harness,
  } = useStore(useShallow(state => ({
    query: state.sessions[sessionId]?.promptDraft ?? '',
    permissionMode: state.permissionMode,
    setPermissionMode: state.setPermissionMode,
    defaultModelConfig: state.modelConfig,
    sessionModelConfig: state.sessions[sessionId]?.modelConfig,
    setSessionModelConfig: state.setSessionModelConfig,
    setSessionPromptDraft: state.setSessionPromptDraft,
    harness: state.harness,
  })));
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [commandSelectedIndex, setCommandSelectedIndex] = useState(0);
  const [fileSelectedIndex, setFileSelectedIndex] = useState(0);
  const [permissionDropdownOpen, setPermissionDropdownOpen] = useState(false);
  const permissionDropdownRef = useRef<HTMLDivElement>(null);
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const modelDropdownRef = useRef<HTMLDivElement>(null);
  const [modelDropdownIndex, setModelDropdownIndex] = useState(0);
  const [projectFiles, setProjectFiles] = useState<string[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [cursorPosition, setCursorPosition] = useState(0);
  const [approvalSelectedIndex, setApprovalSelectedIndex] = useState(0);
  const modelConfig = sessionModelConfig ?? defaultModelConfig;
  const setModelConfig = useCallback((config: Partial<ModelConfig>) => {
    setSessionModelConfig(sessionId, config);
  }, [sessionId, setSessionModelConfig]);
  const setQuery = useCallback((nextQuery: string) => {
    setSessionPromptDraft(sessionId, nextQuery);
  }, [sessionId, setSessionPromptDraft]);

  const hasPendingApproval = Boolean(pendingApproval?.requestId);
  const approvalContent = useMemo(
    () => (pendingApproval ? buildApprovalPromptContent(pendingApproval) : null),
    [pendingApproval],
  );

  const isTypingCommand = query.startsWith('/');
  const commandQuery = isTypingCommand ? query.slice(1).split(/\s/)[0] : '';
  const showCommandAutocomplete = !hasPendingApproval && isTypingCommand && !query.includes(' ');

  const fileIndex = useMemo(() => buildFileSearchIndex(projectFiles), [projectFiles]);

  const fileContext = useMemo(
    () => getFileAutocompleteContext(query, cursorPosition),
    [query, cursorPosition],
  );

  const fileSuggestions = useMemo(
    () => (fileContext ? searchFileSuggestions(fileIndex, fileContext.fileQuery) : []),
    [fileContext, fileIndex],
  );

  const showFileAutocomplete = !hasPendingApproval && fileContext !== null && !showCommandAutocomplete;

  const submitApprovalDecision = useCallback((decision: ApprovalDecision) => {
    if (!pendingApproval?.requestId) return;

    if (decision === 'approve') {
      onApproveApproval?.(pendingApproval.requestId);
      return;
    }

    if (decision === 'auto-approve') {
      onAutoApproveApproval?.(pendingApproval.requestId);
      return;
    }

    onRejectApproval?.(pendingApproval.requestId);
  }, [pendingApproval?.requestId, onApproveApproval, onAutoApproveApproval, onRejectApproval]);

  useEffect(() => {
    if (hasPendingApproval) {
      setPermissionDropdownOpen(false);
      setModelDropdownOpen(false);
    }
  }, [hasPendingApproval]);

  useEffect(() => {
    setApprovalSelectedIndex(0);
  }, [pendingApproval?.requestId]);

  useEffect(() => {
    if (!hasPendingApproval || !approvalContent || !isFocused) return;

    const options = approvalContent.options;

    function handleApprovalKeyDown(e: KeyboardEvent) {
      e.stopPropagation();
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setApprovalSelectedIndex((prev) => (prev + 1) % options.length);
        return;
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setApprovalSelectedIndex((prev) => (prev - 1 + options.length) % options.length);
        return;
      }

      if (e.key === 'Enter') {
        e.preventDefault();
        const selectedOption = options[approvalSelectedIndex];
        if (!selectedOption) return;
        submitApprovalDecision(selectedOption.decision);
        return;
      }

      if (e.key === 'Escape') {
        e.preventDefault();
        submitApprovalDecision('reject');
      }
    }

    document.addEventListener('keydown', handleApprovalKeyDown);
    return () => document.removeEventListener('keydown', handleApprovalKeyDown);
  }, [approvalContent, approvalSelectedIndex, hasPendingApproval, isFocused, submitApprovalDecision]);

  useLayoutEffect(() => {
    if (isFocused && !hasPendingApproval) {
      inputRef.current?.focus();
    }
  }, [busy, isFocused, hasPendingApproval]);

  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;

    el.style.height = 'auto';

    const clampedHeight = Math.min(el.scrollHeight, PROMPT_TEXTAREA_MAX_HEIGHT);
    el.style.height = `${clampedHeight}px`;
    el.style.overflowY = el.scrollHeight > PROMPT_TEXTAREA_MAX_HEIGHT ? 'auto' : 'hidden';
  }, [query]);

  useEffect(() => {
    setCommandSelectedIndex(0);
  }, [commandQuery]);

  useEffect(() => {
    setFileSelectedIndex(0);
  }, [fileContext?.fileQuery]);

  useEffect(() => {
    let cancelled = false;

    if (!projectPath) {
      setProjectFiles([]);
      setFilesLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setFilesLoading(true);

    window.fs.listFiles(projectPath)
      .then((files) => {
        if (cancelled) return;
        setProjectFiles(files);
      })
      .catch((err: unknown) => {
        console.warn('[prompt-bar] Failed to list files:', err);
        if (cancelled) return;
        setProjectFiles([]);
      })
      .finally(() => {
        if (!cancelled) {
          setFilesLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [projectPath]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (permissionDropdownRef.current && !permissionDropdownRef.current.contains(e.target as Node)) {
        setPermissionDropdownOpen(false);
      }
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target as Node)) {
        setModelDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (!isFocused) return;
      if (hasPendingApproval) return;
      if (e.shiftKey && e.key === 'Tab') {
        e.preventDefault();
        void setPermissionMode(permissionMode === 'default' ? 'full' : 'default');
      }
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isFocused, hasPendingApproval, permissionMode, setPermissionMode]);

  const handleCommandSelect = (command: Command) => {
    setQuery(`/${command.name} `);
    inputRef.current?.focus();
  };

  const handleFileSelect = (filePath: string) => {
    if (!fileContext) return;

    const { nextQuery, nextCursor } = insertFileTag(query, filePath, fileContext);

    setQuery(nextQuery);
    setCursorPosition(nextCursor);

    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.selectionStart = nextCursor;
        inputRef.current.selectionEnd = nextCursor;
        inputRef.current.focus();
      }
    }, 0);
  };

  const handleModelSelect = (model: ModelOption) => {
    setModelConfig({ name: model.id, effort: model.effort || 'default' });
    inputRef.current?.focus();
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setQuery(e.target.value);
    setCursorPosition(e.target.selectionStart || 0);
  };

  const handleInputSelect = (e: React.SyntheticEvent<HTMLTextAreaElement>) => {
    setCursorPosition((e.target as HTMLTextAreaElement).selectionStart || 0);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (hasPendingApproval) {
      e.preventDefault();
      return;
    }

    if (showCommandAutocomplete) {
      const count = getFilteredCommandCount(commandQuery);

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setCommandSelectedIndex((prev) => (prev + 1) % count);
        return;
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setCommandSelectedIndex((prev) => (prev - 1 + count) % count);
        return;
      }

      if (e.key === 'Tab') {
        e.preventDefault();
        const command = getCommandAtIndex(commandQuery, commandSelectedIndex);
        if (command) {
          handleCommandSelect(command);
        }
        return;
      }

      if (e.key === 'Escape') {
        e.preventDefault();
        setQuery('');
        return;
      }

      if (e.key === 'Enter') {
        e.preventDefault();
        const command = getCommandAtIndex(commandQuery, commandSelectedIndex);
        if (command) {
          onSubmit(`/${command.name}`);
          setQuery('');
        }
        return;
      }
    }

    if (showFileAutocomplete && fileContext) {
      const count = fileSuggestions.length;

      if (count > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setFileSelectedIndex((prev) => (prev + 1) % count);
          return;
        }

        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setFileSelectedIndex((prev) => (prev - 1 + count) % count);
          return;
        }

        if (e.key === 'Tab' || e.key === 'Enter') {
          e.preventDefault();
          const filePath = fileSuggestions[fileSelectedIndex] ?? null;
          if (filePath) {
            handleFileSelect(filePath);
          }
          return;
        }
      }

      if (filesLoading && (e.key === 'Enter' || e.key === 'Tab')) {
        e.preventDefault();
        return;
      }

      if (e.key === 'Escape') {
        e.preventDefault();
        if (fileContext.atIndex >= 0) {
          const prefix = query.slice(0, fileContext.atIndex);
          const suffix = query.slice(fileContext.tokenEnd);
          setQuery(prefix + suffix);
          setCursorPosition(prefix.length);
        }
        return;
      }
    }

    if (e.key === 'Enter' && !e.shiftKey && query.trim()) {
      e.preventDefault();
      onSubmit(query.trim());
      setQuery('');
    }
  };

  return (
    <div className="relative font-sans text-[13px]">
      {showCommandAutocomplete && (
        <CommandAutocomplete
          query={commandQuery}
          selectedIndex={commandSelectedIndex}
          onSelect={handleCommandSelect}
        />
      )}
      {showFileAutocomplete && fileContext && (
        <FileAutocomplete
          suggestions={fileSuggestions}
          selectedIndex={fileSelectedIndex}
          onSelect={handleFileSelect}
          loading={filesLoading}
        />
      )}

      {pendingImages && pendingImages.length > 0 && (
        <div className="flex gap-2 mb-2 flex-wrap">
          {pendingImages.map((img, i) => (
            <div key={i} className="relative group">
              <img
                src={`data:${img.mimeType};base64,${img.base64}`}
                alt={img.fileName || "pending image"}
                className="w-16 h-16 object-cover rounded border border-gray-600"
              />
              <button
                type="button"
                onClick={() => onRemoveImage?.(i)}
                className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-gray-700 hover:bg-red-600 rounded-full flex items-center justify-center text-gray-300 text-xs leading-none opacity-0 group-hover:opacity-100 transition-opacity"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      <div
        className={`relative border border-[#2a3140] bg-[#172131]/95 px-4 py-3.5 min-h-[106px] flex flex-col shadow-[0_0_0_1px_rgba(255,255,255,0.02)_inset] rounded-2xl ${
          connectedTop ? "-mt-px rounded-t-none border-t-0" : ""
        }`}
      >
        {isDragOver && (
          <div className="pointer-events-none absolute inset-0 rounded-2xl border-2 border-[var(--ui-accent)] bg-[#172131]/70 backdrop-blur-sm z-30 flex items-center justify-center">
            <span className="rounded-md bg-black/20 px-3 py-1.5 text-[color:var(--ui-accent)] text-sm font-medium">Drop images here</span>
          </div>
        )}
        {hasPendingApproval && approvalContent ? (
          <div className="flex flex-col gap-2.5 min-h-[76px]">
            <div className="text-[14px] leading-[1.35] text-[color:var(--ui-text)] font-medium">
              {approvalContent.question}
            </div>

            <div className="rounded-lg bg-black/20 px-3 py-2 font-mono text-[12px] text-[color:var(--ui-text-muted)] break-all">
              {approvalContent.preview}
            </div>

            <div className="space-y-1">
              {approvalContent.options.map((option, index) => {
                const selected = index === approvalSelectedIndex;
                return (
                  <button
                    key={option.label}
                    type="button"
                    onClick={() => {
                      setApprovalSelectedIndex(index);
                      submitApprovalDecision(option.decision);
                    }}
                    className={`w-full rounded-lg px-3 py-1.5 text-left transition-colors flex items-center gap-2 min-w-0 ${
                      selected
                        ? 'bg-[var(--ui-accent-bubble)] text-[color:var(--ui-text)]'
                        : 'hover:bg-white/5 text-[color:var(--ui-text-muted)]'
                    }`}
                  >
                    <span className="w-4 shrink-0 text-[color:var(--ui-text-dim)]">{index + 1}.</span>
                    <span className="min-w-0 truncate">{option.label}</span>
                  </button>
                );
              })}
            </div>

            <div className="mt-auto pt-2 text-xs text-[color:var(--ui-text-dim)] flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
              <div ref={modelDropdownRef} className="relative shrink min-w-0">
                <button
                  type="button"
                  onClick={() => { setModelDropdownOpen(o => !o); setModelDropdownIndex(0); }}
                  className="cursor-pointer text-[color:var(--ui-text-muted)] hover:opacity-80 transition-opacity truncate max-w-[180px]"
                >
                  {MODEL_DISPLAY_NAMES[modelConfig.name] || modelConfig.name}
                </button>
                {modelDropdownOpen && (
                  <div className={`absolute ${dropUp ? 'bottom-full mb-1' : 'top-full mt-1'} left-0 bg-gray-800 border border-gray-700 rounded shadow-lg overflow-hidden z-50`}>
                    {getModelsForHarness(harness).map((model, idx) => {
                      const isCurrent = model.id === modelConfig.name && (model.effort || 'default') === (modelConfig.effort || 'default');
                      return (
                        <button
                          key={`${model.id}-${model.effort ?? ''}`}
                          type="button"
                          onClick={() => { handleModelSelect(model); setModelDropdownOpen(false); }}
                          onMouseEnter={() => setModelDropdownIndex(idx)}
                          className={`block w-full text-left px-3 py-1.5 whitespace-nowrap transition-colors flex items-center gap-2 ${idx === modelDropdownIndex ? 'bg-gray-700' : 'hover:bg-gray-700'} ${isCurrent ? 'text-gray-200' : 'text-gray-400'}`}
                        >
                          {model.name}
                          {isCurrent && <span className="ml-auto pl-3 text-gray-400">✓</span>}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
              <span className="text-[#435069]">·</span>
              <div ref={permissionDropdownRef} className="relative shrink-0">
                <button
                  type="button"
                  onClick={() => setPermissionDropdownOpen(o => !o)}
                  className={`cursor-pointer hover:opacity-80 transition-opacity ${permissionMode === 'full' ? 'text-orange-400' : 'text-[color:var(--ui-text-muted)]'}`}
                >
                  {permissionMode === 'full' ? 'Full access' : 'Default permissions'}
                </button>
                {permissionDropdownOpen && (
                  <div className={`absolute ${dropUp ? 'bottom-full mb-1' : 'top-full mt-1'} left-0 bg-gray-800 border border-gray-700 rounded-2xl shadow-lg overflow-hidden z-50 min-w-[220px]`}>
                    {PERMISSION_OPTIONS.map((option) => {
                      const selected = option.value === permissionMode;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => { void setPermissionMode(option.value); setPermissionDropdownOpen(false); }}
                          className={`block w-full text-left px-3 py-2 hover:bg-gray-700 transition-colors flex items-center gap-2 ${selected ? 'text-gray-200' : 'text-gray-400'}`}
                        >
                          <span className="w-4 text-center">{selected ? '✓' : ''}</span>
                          <span>{option.label}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
              <span className="ml-auto" />
              <ContextIndicator sessionId={sessionId} />
              {gitBranch && projectPath && (
                <>
                  <WorktreeSelector
                    projectPath={mainProjectPath || projectPath}
                    gitBranch={gitBranch}
                    worktreeType={worktreeType}
                    worktreePath={worktreePath}
                    tabId={tabId}
                    dropUp={dropUp}
                  />
                  {githubPR && (
                    <>
                      <span className="text-[#435069]">·</span>
                      <a
                        href={githubPR.url}
                        onClick={e => { e.preventDefault(); window.open(githubPR.url, '_blank'); }}
                        className="text-[color:var(--ui-text-muted)] hover:text-[#87CEEB] transition-colors shrink-0"
                      >
                        #{githubPR.number}
                      </a>
                    </>
                  )}
                </>
              )}
              <span className="text-[#435069]">·</span>
              <button
                type="button"
                onClick={() => submitApprovalDecision('reject')}
                className="text-[color:var(--ui-text-muted)] hover:text-[color:var(--ui-text)] transition-colors cursor-pointer"
              >
                Skip
              </button>
            </div>
          </div>
        ) : (
          <>
            <textarea
              ref={inputRef}
              rows={1}
              value={query}
              onChange={handleInputChange}
              onSelect={handleInputSelect}
              onKeyDown={handleKeyDown}
              placeholder={busy ? "Send a message to queue next..." : "Ask Open SWE anything, @ to add files, / for commands"}
              className="w-full min-h-[52px] bg-transparent text-[color:var(--ui-text)] outline-none placeholder-[color:var(--ui-text-dim)] resize-none overflow-hidden leading-[1.45] min-w-0"
              style={{ maxHeight: PROMPT_TEXTAREA_MAX_HEIGHT }}
            />

            <div className="mt-auto pt-2 text-xs text-[color:var(--ui-text-dim)] flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
              <div ref={modelDropdownRef} className="relative shrink min-w-0">
                <button
                  type="button"
                  onClick={() => { setModelDropdownOpen(o => !o); setModelDropdownIndex(0); }}
                  className="cursor-pointer text-[color:var(--ui-text-muted)] hover:opacity-80 transition-opacity truncate max-w-[180px]"
                >
                  {MODEL_DISPLAY_NAMES[modelConfig.name] || modelConfig.name}
                </button>
                {modelDropdownOpen && (
                  <div className={`absolute ${dropUp ? 'bottom-full mb-1' : 'top-full mt-1'} left-0 bg-gray-800 border border-gray-700 rounded shadow-lg overflow-hidden z-50`}>
                    {getModelsForHarness(harness).map((model, idx) => {
                      const isCurrent = model.id === modelConfig.name && (model.effort || 'default') === (modelConfig.effort || 'default');
                      return (
                        <button
                          key={`${model.id}-${model.effort ?? ''}`}
                          type="button"
                          onClick={() => { handleModelSelect(model); setModelDropdownOpen(false); }}
                          onMouseEnter={() => setModelDropdownIndex(idx)}
                          className={`block w-full text-left px-3 py-1.5 whitespace-nowrap transition-colors flex items-center gap-2 ${idx === modelDropdownIndex ? 'bg-gray-700' : 'hover:bg-gray-700'} ${isCurrent ? 'text-gray-200' : 'text-gray-400'}`}
                        >
                          {model.name}
                          {isCurrent && <span className="ml-auto pl-3 text-gray-400">✓</span>}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
              <span className="text-[#435069]">·</span>
              <div ref={permissionDropdownRef} className="relative shrink-0">
                <button
                  type="button"
                  onClick={() => setPermissionDropdownOpen(o => !o)}
                  className={`cursor-pointer hover:opacity-80 transition-opacity ${permissionMode === 'full' ? 'text-orange-400' : 'text-[color:var(--ui-text-muted)]'}`}
                >
                  {permissionMode === 'full' ? 'Full access' : 'Default permissions'}
                </button>
                {permissionDropdownOpen && (
                  <div className={`absolute ${dropUp ? 'bottom-full mb-1' : 'top-full mt-1'} left-0 bg-gray-800 border border-gray-700 rounded-2xl shadow-lg overflow-hidden z-50 min-w-[220px]`}>
                    {PERMISSION_OPTIONS.map((option) => {
                      const selected = option.value === permissionMode;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => { void setPermissionMode(option.value); setPermissionDropdownOpen(false); }}
                          className={`block w-full text-left px-3 py-2 hover:bg-gray-700 transition-colors flex items-center gap-2 ${selected ? 'text-gray-200' : 'text-gray-400'}`}
                        >
                          <span className="w-4 text-center">{selected ? '✓' : ''}</span>
                          <span>{option.label}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
              <span className="ml-auto" />
              <ContextIndicator sessionId={sessionId} />
              {gitBranch && projectPath && (
                <>
                  <WorktreeSelector
                    projectPath={mainProjectPath || projectPath}
                    gitBranch={gitBranch}
                    worktreeType={worktreeType}
                    worktreePath={worktreePath}
                    tabId={tabId}
                    dropUp={dropUp}
                  />
                  {githubPR && (
                    <>
                      <span className="text-[#435069]">·</span>
                      <a
                        href={githubPR.url}
                        onClick={e => { e.preventDefault(); window.open(githubPR.url, '_blank'); }}
                        className="text-[color:var(--ui-text-muted)] hover:text-[#87CEEB] transition-colors shrink-0"
                      >
                        #{githubPR.number}
                      </a>
                    </>
                  )}
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
});
