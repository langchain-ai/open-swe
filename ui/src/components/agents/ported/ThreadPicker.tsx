import { MessageSquare } from 'lucide-react';
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import type { Thread } from '@/lib/agents/types';
import { useStore } from '../../store';

function getTimeGroup(timestamp: number): string {
  const now = Date.now();
  const diff = now - timestamp;
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));

  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days}d ago`;
  if (days < 14) return '1w ago';
  return `${Math.floor(days / 7)}w ago`;
}

function groupThreadsByTime(threads: Thread[]): { group: string; threads: Thread[] }[] {
  const map = new Map<string, Thread[]>();
  const sorted = [...threads].sort((a, b) => b.updatedAt - a.updatedAt);

  for (const thread of sorted) {
    const group = getTimeGroup(thread.updatedAt);
    const existing = map.get(group) || [];
    map.set(group, [...existing, thread]);
  }

  return Array.from(map.entries()).map(([group, threads]) => ({ group, threads }));
}

interface ThreadPickerProps {
  projectId: string;
  onSelect: (thread: Thread) => void;
  onClose: () => void;
}

export function ThreadPicker({ projectId, onSelect, onClose }: ThreadPickerProps) {
  const tabs = useStore((state) => state.tabs);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [search, setSearch] = useState('');
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const activeSessionIds = useMemo(
    () => new Set(Object.values(tabs).map((tab) => tab.sessionId)),
    [tabs],
  );

  useEffect(() => {
    window.storage.loadThreadsForProject(projectId).then((loaded) => {
      setThreads(
        loaded
          .filter((thread) => !activeSessionIds.has(thread.id))
          .sort((a, b) => b.updatedAt - a.updatedAt),
      );
      setLoading(false);
    });
  }, [projectId, activeSessionIds]);

  const filtered = search
    ? threads.filter((t) => t.title.toLowerCase().includes(search.toLowerCase()))
    : threads;

  const groups = groupThreadsByTime(filtered);
  const flatList = groups.flatMap((g) => g.threads);

  // Reset selection when search changes
  useEffect(() => {
    setSelectedIndex(0);
  }, [search]);

  // Scroll selected item into view
  useEffect(() => {
    const selected = listRef.current?.querySelector('[data-selected="true"]');
    selected?.scrollIntoView({ block: 'nearest' });
  }, [selectedIndex]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        return;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((prev) => Math.min(prev + 1, flatList.length - 1));
        return;
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((prev) => Math.max(prev - 1, 0));
        return;
      }

      if (e.key === 'Enter' && flatList.length > 0) {
        e.preventDefault();
        onSelect(flatList[selectedIndex]);
        return;
      }
    },
    [flatList, selectedIndex, onSelect, onClose],
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown, true);
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [handleKeyDown]);

  // Auto-focus search
  useEffect(() => {
    searchRef.current?.focus();
  }, [loading]);

  const messageCount = (thread: Thread) => {
    const count = thread.messages.filter((m) => m.author === 'user' || m.author === 'agent').length;
    return `${count} msg${count !== 1 ? 's' : ''}`;
  };

  const formatDate = (timestamp: number) => {
    const d = new Date(timestamp);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  let currentFlatIndex = 0;

  return (
    <div className="absolute inset-0 z-40 bg-[#1a2332]/95 flex flex-col items-center pt-16 px-4">
      <div className="w-full max-w-lg flex flex-col bg-[#1e2a3a] border border-gray-700 rounded-lg shadow-2xl overflow-hidden max-h-[70vh]">
        <div className="p-3 border-b border-gray-700 shrink-0">
          <input
            ref={searchRef}
            type="text"
            placeholder="Search conversations..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-[#1a1f2e] text-gray-200 text-sm px-3 py-2 rounded-md border border-gray-600 focus:outline-none focus:border-[#5a9bc7]"
          />
        </div>

        <div ref={listRef} className="overflow-y-auto flex-1">
          {loading ? (
            <div className="p-6 text-gray-500 text-sm text-center">Loading...</div>
          ) : flatList.length === 0 ? (
            <div className="p-6 text-gray-500 text-sm text-center">
              {search ? 'No matching conversations' : 'No previous conversations for this project'}
            </div>
          ) : (
            groups.map(({ group, threads: groupThreads }) => (
              <div key={group}>
                <div className="px-4 py-2 text-xs text-gray-500 font-medium sticky top-0 bg-[#1e2a3a]">
                  {group}
                </div>
                {groupThreads.map((thread) => {
                  const idx = currentFlatIndex++;
                  const isSelected = idx === selectedIndex;

                  return (
                    <button
                      key={thread.id}
                      data-selected={isSelected}
                      onClick={() => onSelect(thread)}
                      className={`w-full px-4 py-3 text-left flex items-center gap-3 transition-colors ${
                        isSelected ? 'bg-[#2a3142]' : 'hover:bg-[#252a3a]'
                      }`}
                    >
                      <ChatIcon />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm text-gray-200 truncate">{thread.title}</div>
                        <div className="text-xs text-gray-500 mt-0.5">
                          {messageCount(thread)} — {formatDate(thread.updatedAt)}
                        </div>
                      </div>
                      {isSelected && (
                        <span className="text-xs text-gray-500 shrink-0">Enter</span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        <div className="px-4 py-2 border-t border-gray-700 text-xs text-gray-500 flex gap-4 shrink-0">
          <span><kbd className="px-1 py-0.5 bg-[#1a1f2e] rounded text-gray-400">Up/Down</kbd> navigate</span>
          <span><kbd className="px-1 py-0.5 bg-[#1a1f2e] rounded text-gray-400">Enter</kbd> select</span>
          <span><kbd className="px-1 py-0.5 bg-[#1a1f2e] rounded text-gray-400">Esc</kbd> cancel</span>
        </div>
      </div>
    </div>
  );
}

function ChatIcon() {
  return <MessageSquare size={14} strokeWidth={2} className="text-gray-500 flex-shrink-0" />;
}
