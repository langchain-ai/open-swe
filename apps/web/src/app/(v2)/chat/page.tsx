"use client";

import { DefaultView } from "@/components/default-view";
import { useRouter } from "next/navigation";
import type { Thread, ThreadDisplayInfo } from "@/types";
import { threadToDisplayInfo } from "@/utils/thread-utils";

// Mock data using the new Thread interface
const mockThreads: Thread[] = [
  {
    thread_id: "1",
    created_at: "2024-01-15T10:00:00Z",
    updated_at: "2024-01-15T10:02:00Z",
    metadata: {},
    status: "busy",
    values: {
      messages: [
        {
          content:
            "I need to update the GitHub access tokens in the proxy route",
          type: "human",
        },
        {
          content: "I'll help you update the GitHub access tokens securely",
          type: "ai",
        },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-1",
            taskIndex: 0,
            request: "Update GitHub access tokens in proxy route",
            createdAt: Date.now() - 120000,
            completed: false,
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  {
                    index: 0,
                    plan: "Analyze current token implementation",
                    completed: true,
                  },
                  {
                    index: 1,
                    plan: "Implement secure token encryption",
                    completed: false,
                  },
                  {
                    index: 2,
                    plan: "Update proxy route handlers",
                    completed: false,
                  },
                  { index: 3, plan: "Add error handling", completed: false },
                ],
                createdAt: Date.now() - 120000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary: "Working on GitHub token security improvements",
      sandboxSessionId: "sandbox-1",
      branchName: "feature/secure-tokens",
      targetRepository: {
        owner: "open-swe",
        repo: "main",
        branch: "main",
      },
      codebaseTree: "src/...",
      githubIssueId: 123,
    },
    interrupts: {},
  },
  {
    thread_id: "2",
    created_at: "2024-01-15T09:30:00Z",
    updated_at: "2024-01-15T09:47:00Z",
    metadata: {},
    status: "idle",
    values: {
      messages: [
        {
          content: "Encrypt GitHub access tokens before forwarding",
          type: "human",
        },
        { content: "Task completed successfully!", type: "ai" },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-2",
            taskIndex: 0,
            request: "Encrypt GitHub access tokens before forwarding",
            createdAt: Date.now() - 1800000,
            completed: true,
            completedAt: Date.now() - 780000,
            summary: "Successfully implemented token encryption",
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  {
                    index: 0,
                    plan: "Research encryption methods",
                    completed: true,
                  },
                  {
                    index: 1,
                    plan: "Implement AES encryption",
                    completed: true,
                  },
                  {
                    index: 2,
                    plan: "Update forwarding logic",
                    completed: true,
                  },
                  { index: 3, plan: "Add tests", completed: true },
                ],
                createdAt: Date.now() - 1800000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary: "Completed token encryption implementation",
      sandboxSessionId: "sandbox-2",
      branchName: "feature/token-encryption",
      targetRepository: {
        owner: "open-swe",
        repo: "main",
        branch: "main",
      },
      codebaseTree: "src/...",
      githubIssueId: 121,
    },
    interrupts: {},
  },
  {
    thread_id: "3",
    created_at: "2024-01-15T08:30:00Z",
    updated_at: "2024-01-15T08:43:00Z",
    metadata: {},
    status: "error",
    values: {
      messages: [
        { content: "Thread 01bec8f optimization", type: "human" },
        { content: "Encountered an error during optimization", type: "ai" },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-3",
            taskIndex: 0,
            request: "Thread 01bec8f optimization",
            createdAt: Date.now() - 3600000,
            completed: false,
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  {
                    index: 0,
                    plan: "Analyze thread performance",
                    completed: false,
                  },
                ],
                createdAt: Date.now() - 3600000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary: "Failed thread optimization",
      sandboxSessionId: "sandbox-3",
      branchName: "main",
      targetRepository: {
        owner: "x",
        repo: "x",
        branch: "main",
      },
      codebaseTree: "src/...",
      githubIssueId: 45,
    },
    interrupts: {},
  },
  {
    thread_id: "4",
    created_at: "2024-01-15T07:30:00Z",
    updated_at: "2024-01-15T07:42:00Z",
    metadata: {},
    status: "busy",
    values: {
      messages: [
        { content: "Fix thread loading performance issues", type: "human" },
        { content: "Working on performance improvements", type: "ai" },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-4",
            taskIndex: 0,
            request: "Fix thread loading performance issues",
            createdAt: Date.now() - 4800000,
            completed: false,
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  {
                    index: 0,
                    plan: "Profile current loading times",
                    completed: true,
                  },
                  {
                    index: 1,
                    plan: "Optimize data fetching",
                    completed: false,
                  },
                  {
                    index: 2,
                    plan: "Implement caching strategies",
                    completed: false,
                  },
                  {
                    index: 3,
                    plan: "Test performance improvements",
                    completed: false,
                  },
                  { index: 4, plan: "Deploy optimizations", completed: false },
                  {
                    index: 5,
                    plan: "Monitor performance metrics",
                    completed: false,
                  },
                ],
                createdAt: Date.now() - 4800000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary: "Fixing thread loading performance issues",
      sandboxSessionId: "sandbox-4",
      branchName: "main",
      targetRepository: {
        owner: "open-swe",
        repo: "main",
        branch: "main",
      },
      codebaseTree: "src/...",
      githubIssueId: 125,
    },
    interrupts: {},
  },
  {
    thread_id: "5",
    created_at: "2024-01-15T06:30:00Z",
    updated_at: "2024-01-15T06:40:00Z",
    metadata: {},
    status: "idle",
    values: {
      messages: [
        { content: "Add repo/branch name to thread selector", type: "human" },
        { content: "Repo/branch name added to thread selector", type: "ai" },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-5",
            taskIndex: 0,
            request: "Add repo/branch name to thread selector",
            createdAt: Date.now() - 7200000,
            completed: true,
            completedAt: Date.now() - 6600000,
            summary:
              "Successfully added repo/branch display to thread selector",
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  { index: 0, plan: "Design UI changes", completed: true },
                  { index: 1, plan: "Implement UI changes", completed: true },
                  { index: 2, plan: "Test UI changes", completed: true },
                  { index: 3, plan: "Deploy UI changes", completed: true },
                  { index: 4, plan: "Verify functionality", completed: true },
                ],
                createdAt: Date.now() - 7200000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary:
        "Completed adding repo/branch name to thread selector",
      sandboxSessionId: "sandbox-5",
      branchName: "main",
      targetRepository: {
        owner: "open-swe",
        repo: "main",
        branch: "main",
      },
      codebaseTree: "src/...",
      githubIssueId: 119,
    },
    interrupts: {},
  },
];

export default function ChatPage() {
  const router = useRouter();

  // Convert Thread objects to ThreadDisplayInfo for UI
  const displayThreads: ThreadDisplayInfo[] =
    mockThreads.map(threadToDisplayInfo);

  const handleThreadSelect = (thread: ThreadDisplayInfo) => {
    router.push(`/chat/${thread.id}`);
  };

  return (
    <div className="h-screen bg-black">
      <DefaultView
        threads={displayThreads}
        onThreadSelect={handleThreadSelect}
      />
    </div>
  );
}
