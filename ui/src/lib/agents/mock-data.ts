import type { AgentThread, Message } from "./types";

const now = Date.now();
const hours = (n: number) => now - n * 60 * 60 * 1000;
const days = (n: number) => now - n * 24 * 60 * 60 * 1000;
const weeks = (n: number) => now - n * 7 * 24 * 60 * 60 * 1000;

const fileCommentsMessages: Message[] = [
  {
    id: "m1",
    author: "user",
    timestamp: new Date(hours(4)).toISOString(),
    chunks: [{ kind: "text", text: "Add test comments to the frontend modules" }],
  },
  {
    id: "m2",
    author: "agent",
    timestamp: new Date(hours(4)).toISOString(),
    chunks: [
      { kind: "text", text: "Environment ready" },
      {
        kind: "tool-execution",
        toolCallId: "t1",
        title: "Edited app.js",
        toolKind: "edit",
        status: "completed",
        diffData: {
          originalContent: "export function init() {\n  mount();\n}\n",
          newContent: "export function init() {\n  // test comment\n  mount();\n}\n",
          filePath: "app.js",
          isNewFile: false,
          isBinary: false,
          isTruncated: false,
          totalLines: 4,
        },
      },
      {
        kind: "tool-execution",
        toolCallId: "t2",
        title: "Edited contextPanel.js",
        toolKind: "edit",
        status: "completed",
        diffData: {
          originalContent: "export function renderPanel() {\n  return panel;\n}\n",
          newContent: "export function renderPanel() {\n  // test comment\n  return panel;\n}\n",
          filePath: "contextPanel.js",
          isNewFile: false,
          isBinary: false,
          isTruncated: false,
          totalLines: 4,
        },
      },
      {
        kind: "tool-execution",
        toolCallId: "t3",
        title: "Edited settings.js",
        toolKind: "edit",
        status: "completed",
        diffData: {
          originalContent: "export const defaults = {};\n",
          newContent: "export const defaults = {};\n// test comment\n",
          filePath: "settings.js",
          isNewFile: false,
          isBinary: false,
          isTruncated: false,
          totalLines: 2,
        },
      },
      {
        kind: "text",
        text: "Done — added test comments to `app.js`, `contextPanel.js`, and `settings.js`, opened draft PR #1, and pushed branch `johannes/add-test-comments-571c`.",
      },
    ],
  },
];

export const MOCK_THREADS: AgentThread[] = [
  {
    id: "file-comments-test",
    title: "File comments test",
    repo: "chat-studio",
    repoFullName: "johannes117/chat-studio",
    branch: "main",
    model: "GPT-5.5 High",
    status: "finished",
    createdAt: hours(4),
    updatedAt: hours(4),
    messages: fileCommentsMessages,
    pr: {
      number: 1,
      title: "Add test comments to frontend modules",
      state: "draft",
      headRef: "johannes/add-test-comments-571c",
      baseRef: "main",
      url: "https://github.com/johannes117/chat-studio/pull/1",
    },
    diffStats: { files: 3, additions: 3, deletions: 0 },
    changedFiles: [
      {
        path: "app.js",
        additions: 1,
        deletions: 0,
        patch: "@@ -1,3 +1,4 @@\n export function init() {\n+  // test comment\n   mount();\n }",
      },
      {
        path: "contextPanel.js",
        additions: 1,
        deletions: 0,
        patch: "@@ -1,3 +1,4 @@\n export function renderPanel() {\n+  // test comment\n   return panel;\n }",
      },
      {
        path: "settings.js",
        additions: 1,
        deletions: 0,
        patch: "@@ -1,1 +1,2 @@\n export const defaults = {};\n+// test comment",
      },
    ],
  },
  {
    id: "system-health-check",
    title: "System health check",
    repo: "chat-studio",
    repoFullName: "johannes117/chat-studio",
    branch: "main",
    model: "GPT-5.5 High",
    status: "finished",
    createdAt: days(2),
    updatedAt: days(2),
    messages: [],
    diffStats: { files: 3, additions: 3, deletions: 0 },
  },
  {
    id: "devin-pr-comments",
    title: "Devin pr comments",
    repo: "langchainplus",
    repoFullName: "langchain-ai/langchainplus",
    branch: "main",
    model: "Opus 4.6 High",
    status: "finished",
    createdAt: weeks(2),
    updatedAt: weeks(2),
    messages: [],
    diffStats: { files: 23, additions: 740, deletions: 185 },
  },
  {
    id: "devin-pr-comments-2",
    title: "Devin pr comments",
    repo: "langchainplus",
    repoFullName: "langchain-ai/langchainplus",
    branch: "main",
    model: "Opus 4.6 High",
    status: "finished",
    createdAt: weeks(3),
    updatedAt: weeks(3),
    messages: [],
    diffStats: { files: 23, additions: 740, deletions: 185 },
  },
];

export function getThread(id: string): AgentThread | undefined {
  return MOCK_THREADS.find((t) => t.id === id);
}

export function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  const weeks = Math.floor(days / 7);
  return `${weeks}w`;
}

export type ThreadGroup = "today" | "last30" | "older";

export function groupThreads(threads: AgentThread[]): Record<ThreadGroup, AgentThread[]> {
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const thirtyDaysAgo = Date.now() - 30 * 24 * 60 * 60 * 1000;

  const groups: Record<ThreadGroup, AgentThread[]> = {
    today: [],
    last30: [],
    older: [],
  };

  for (const thread of [...threads].sort((a, b) => b.updatedAt - a.updatedAt)) {
    if (thread.updatedAt >= todayStart.getTime()) {
      groups.today.push(thread);
    } else if (thread.updatedAt >= thirtyDaysAgo) {
      groups.last30.push(thread);
    } else {
      groups.older.push(thread);
    }
  }

  return groups;
}
