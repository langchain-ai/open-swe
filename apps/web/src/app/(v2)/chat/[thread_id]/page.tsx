"use client";

import { ThreadView } from "@/components/v2/thread-view";
import { ThreadDisplayInfo } from "@/components/v2/types";
import { threadToDisplayInfo } from "@/components/v2/utils/thread-utils";
import { Thread } from "@langchain/langgraph-sdk";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { useRouter } from "next/navigation";
import { notFound } from "next/navigation";

// Mock data using the new Thread interface
const mockThreads: Thread<GraphState>[] = [
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
            "I need to update the GitHub access tokens in the proxy route. The current implementation isn't secure enough.",
          type: "human",
          timestamp: "2 min ago",
        },
        {
          content:
            "I'll help you update the GitHub access tokens in the proxy route. Let me analyze the current implementation and create a secure solution.",
          type: "ai",
          timestamp: "2 min ago",
        },
        {
          content:
            "I've identified the security issues and created a plan to encrypt the tokens before forwarding. I'll also add proper error handling and validation.",
          type: "ai",
          timestamp: "1 min ago",
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
                    summary: "Found security vulnerabilities in token handling",
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
                  {
                    index: 3,
                    plan: "Add error handling and validation",
                    completed: false,
                  },
                  {
                    index: 4,
                    plan: "Test the updated implementation",
                    completed: false,
                  },
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
];

interface ThreadPageProps {
  params: {
    thread_id: string;
  };
}

export default function ThreadPage({ params }: ThreadPageProps) {
  const router = useRouter();
  const { thread_id } = params;

  // Find the thread by ID
  const thread = mockThreads.find((t) => t.thread_id === thread_id);

  // If thread not found, show 404
  if (!thread) {
    notFound();
  }

  // Convert all threads to display format
  const displayThreads: ThreadDisplayInfo[] =
    mockThreads.map(threadToDisplayInfo);
  const currentDisplayThread = threadToDisplayInfo(thread);

  const handleThreadSelect = (selectedThread: ThreadDisplayInfo) => {
    router.push(`/chat/${selectedThread.id}`);
  };

  const handleBackToHome = () => {
    router.push("/chat");
  };

  return (
    <div className="h-screen bg-black">
      <ThreadView
        thread={thread}
        displayThread={currentDisplayThread}
        allDisplayThreads={displayThreads}
        onThreadSelect={handleThreadSelect}
        onBackToHome={handleBackToHome}
      />
    </div>
  );
}
