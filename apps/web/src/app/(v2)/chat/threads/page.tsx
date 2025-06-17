"use client"

import type React from "react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  ArrowLeft,
  Search,
  Filter,
  CheckCircle,
  XCircle,
  Loader2,
  GitBranch,
  GitPullRequest,
  Bug,
  Calendar,
  Clock,
} from "lucide-react"
import type { Thread, ThreadDisplayInfo } from "@/types"
import { useRouter } from "next/navigation"
import { threadToDisplayInfo } from "@/utils/thread-utils"

// Mock data using the new Thread interface (same as in chat/page.tsx)
const mockThreads: Thread[] = [
  {
    thread_id: "1",
    created_at: "2024-01-15T10:00:00Z",
    updated_at: "2024-01-15T10:02:00Z",
    metadata: {},
    status: "busy",
    values: {
      messages: [
        { content: "I need to update the GitHub access tokens in the proxy route", type: "human" },
        { content: "I'll help you update the GitHub access tokens securely", type: "ai" },
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
                  { index: 0, plan: "Analyze current token implementation", completed: true },
                  { index: 1, plan: "Implement secure token encryption", completed: false },
                  { index: 2, plan: "Update proxy route handlers", completed: false },
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
    created_at: "2024-01-15T09:00:00Z",
    updated_at: "2024-01-15T09:13:00Z",
    metadata: {},
    status: "idle",
    values: {
      messages: [
        { content: "Encrypt GitHub access tokens before forwarding", type: "human" },
        { content: "Tokens are now encrypted before being forwarded", type: "ai" },
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
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  { index: 0, plan: "Identify sensitive data", completed: true },
                  { index: 1, plan: "Implement encryption logic", completed: true },
                  { index: 2, plan: "Test encryption functionality", completed: true },
                  { index: 3, plan: "Deploy encryption changes", completed: true },
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
      planContextSummary: "Completed GitHub token encryption",
      sandboxSessionId: "sandbox-2",
      branchName: "main",
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
    created_at: "2024-01-15T08:00:00Z",
    updated_at: "2024-01-15T08:15:00Z",
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
                plans: [{ index: 0, plan: "Analyze thread performance", completed: false }],
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
    created_at: "2024-01-15T07:00:00Z",
    updated_at: "2024-01-15T07:12:00Z",
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
                  { index: 0, plan: "Profile current loading times", completed: true },
                  { index: 1, plan: "Optimize data fetching", completed: false },
                  { index: 2, plan: "Implement caching strategies", completed: false },
                  { index: 3, plan: "Test performance improvements", completed: false },
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
    created_at: "2024-01-15T06:00:00Z",
    updated_at: "2024-01-15T06:10:00Z",
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
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  { index: 0, plan: "Design UI changes", completed: true },
                  { index: 1, plan: "Implement UI changes", completed: true },
                  { index: 2, plan: "Test UI changes", completed: true },
                  { index: 3, plan: "Deploy UI changes", completed: true },
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
      planContextSummary: "Completed adding repo/branch name to thread selector",
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
  {
    thread_id: "6",
    created_at: "2024-01-15T05:00:00Z",
    updated_at: "2024-01-15T05:59:00Z",
    metadata: {},
    status: "idle",
    values: {
      messages: [
        { content: "Implement user authentication system", type: "human" },
        { content: "User authentication system implemented", type: "ai" },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-6",
            taskIndex: 0,
            request: "Implement user authentication system",
            createdAt: Date.now() - 36000000,
            completed: true,
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  { index: 0, plan: "Define authentication requirements", completed: true },
                  { index: 1, plan: "Design authentication flow", completed: true },
                  { index: 2, plan: "Implement authentication logic", completed: true },
                  { index: 3, plan: "Test authentication system", completed: true },
                ],
                createdAt: Date.now() - 36000000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary: "Completed implementing user authentication system",
      sandboxSessionId: "sandbox-6",
      branchName: "feature/auth",
      targetRepository: {
        owner: "myapp",
        repo: "frontend",
        branch: "feature/auth",
      },
      codebaseTree: "src/...",
      githubIssueId: 67,
    },
    interrupts: {},
  },
  {
    thread_id: "7",
    created_at: "2024-01-15T04:00:00Z",
    updated_at: "2024-01-15T04:18:00Z",
    metadata: {},
    status: "error",
    values: {
      messages: [
        { content: "Database migration for user profiles", type: "human" },
        { content: "Database migration failed", type: "ai" },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-7",
            taskIndex: 0,
            request: "Database migration for user profiles",
            createdAt: Date.now() - 72000000,
            completed: false,
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  { index: 0, plan: "Backup current database", completed: true },
                  { index: 1, plan: "Design migration plan", completed: false },
                  { index: 2, plan: "Execute migration", completed: false },
                  { index: 3, plan: "Verify migration results", completed: false },
                ],
                createdAt: Date.now() - 72000000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary: "Failed database migration for user profiles",
      sandboxSessionId: "sandbox-7",
      branchName: "main",
      targetRepository: {
        owner: "myapp",
        repo: "backend",
        branch: "main",
      },
      codebaseTree: "src/...",
      githubIssueId: 89,
    },
    interrupts: {},
  },
  {
    thread_id: "8",
    created_at: "2024-01-15T03:00:00Z",
    updated_at: "2024-01-15T03:45:00Z",
    metadata: {},
    status: "busy",
    values: {
      messages: [
        { content: "Add dark mode toggle component", type: "human" },
        { content: "Working on dark mode toggle component", type: "ai" },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-8",
            taskIndex: 0,
            request: "Add dark mode toggle component",
            createdAt: Date.now() - 108000000,
            completed: false,
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  { index: 0, plan: "Design dark mode UI", completed: true },
                  { index: 1, plan: "Implement dark mode styles", completed: false },
                  { index: 2, plan: "Test dark mode functionality", completed: false },
                  { index: 3, plan: "Deploy dark mode changes", completed: false },
                ],
                createdAt: Date.now() - 108000000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary: "Adding dark mode toggle component",
      sandboxSessionId: "sandbox-8",
      branchName: "feature/dark-mode",
      targetRepository: {
        owner: "design-system",
        repo: "ui",
        branch: "feature/dark-mode",
      },
      codebaseTree: "src/...",
      githubIssueId: 0,
    },
    interrupts: {},
  },
  {
    thread_id: "9",
    created_at: "2024-01-15T02:00:00Z",
    updated_at: "2024-01-15T02:30:00Z",
    metadata: {},
    status: "idle",
    values: {
      messages: [
        { content: "Optimize image loading performance", type: "human" },
        { content: "Image loading performance optimized", type: "ai" },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-9",
            taskIndex: 0,
            request: "Optimize image loading performance",
            createdAt: Date.now() - 144000000,
            completed: true,
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  { index: 0, plan: "Analyze current image loading", completed: true },
                  { index: 1, plan: "Implement lazy loading", completed: true },
                  { index: 2, plan: "Test lazy loading", completed: true },
                  { index: 3, plan: "Deploy lazy loading changes", completed: true },
                ],
                createdAt: Date.now() - 144000000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary: "Completed optimizing image loading performance",
      sandboxSessionId: "sandbox-9",
      branchName: "main",
      targetRepository: {
        owner: "myapp",
        repo: "frontend",
        branch: "main",
      },
      codebaseTree: "src/...",
      githubIssueId: 0,
    },
    interrupts: {},
  },
  {
    thread_id: "10",
    created_at: "2024-01-15T01:00:00Z",
    updated_at: "2024-01-14T23:00:00Z",
    metadata: {},
    status: "interrupted",
    values: {
      messages: [
        { content: "Setup CI/CD pipeline", type: "human" },
        { content: "Waiting for CI/CD pipeline setup", type: "ai" },
      ],
      internalMessages: [],
      taskPlan: {
        tasks: [
          {
            id: "task-10",
            taskIndex: 0,
            request: "Setup CI/CD pipeline",
            createdAt: Date.now() - 172800000,
            completed: false,
            planRevisions: [
              {
                revisionIndex: 0,
                plans: [
                  { index: 0, plan: "Define pipeline requirements", completed: true },
                  { index: 1, plan: "Configure CI/CD tools", completed: false },
                  { index: 2, plan: "Test pipeline functionality", completed: false },
                  { index: 3, plan: "Deploy pipeline changes", completed: false },
                ],
                createdAt: Date.now() - 172800000,
                createdBy: "agent",
              },
            ],
            activeRevisionIndex: 0,
          },
        ],
        activeTaskIndex: 0,
      },
      planContextSummary: "Pending CI/CD pipeline setup",
      sandboxSessionId: "sandbox-10",
      branchName: "main",
      targetRepository: {
        owner: "devops",
        repo: "infrastructure",
        branch: "main",
      },
      codebaseTree: "src/...",
      githubIssueId: 0,
    },
    interrupts: {},
  },
]

type FilterStatus = "all" | "running" | "completed" | "failed" | "pending"

export default function AllThreadsPage() {
  const router = useRouter()
  const [searchQuery, setSearchQuery] = useState("")
  const [statusFilter, setStatusFilter] = useState<FilterStatus>("all")

  // Convert Thread objects to ThreadDisplayInfo for UI
  const displayThreads: ThreadDisplayInfo[] = mockThreads.map(threadToDisplayInfo)

  const getStatusColor = (status: ThreadDisplayInfo["status"]) => {
    switch (status) {
      case "running":
        return "bg-blue-950 text-blue-400"
      case "completed":
        return "bg-green-950 text-green-400"
      case "failed":
        return "bg-red-950 text-red-400"
      case "pending":
        return "bg-yellow-950 text-yellow-400"
      default:
        return "bg-gray-800 text-gray-400"
    }
  }

  const getStatusIcon = (status: ThreadDisplayInfo["status"]) => {
    switch (status) {
      case "running":
        return <Loader2 className="h-4 w-4 animate-spin" />
      case "completed":
        return <CheckCircle className="h-4 w-4" />
      case "failed":
        return <XCircle className="h-4 w-4" />
      case "pending":
        return <Clock className="h-4 w-4" />
      default:
        return null
    }
  }

  const getPRStatusColor = (status: string) => {
    switch (status) {
      case "merged":
        return "text-purple-400"
      case "open":
        return "text-green-400"
      case "draft":
        return "text-gray-400"
      case "closed":
        return "text-red-400"
      default:
        return "text-gray-400"
    }
  }

  // Filter and search threads
  const filteredThreads = displayThreads.filter((thread) => {
    const matchesSearch =
      thread.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      thread.repository.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesStatus = statusFilter === "all" || thread.status === statusFilter
    return matchesSearch && matchesStatus
  })

  // Group threads by status
  const groupedThreads = {
    running: filteredThreads.filter((t) => t.status === "running"),
    completed: filteredThreads.filter((t) => t.status === "completed"),
    failed: filteredThreads.filter((t) => t.status === "failed"),
    pending: filteredThreads.filter((t) => t.status === "pending"),
  }

  const statusCounts = {
    all: displayThreads.length,
    running: displayThreads.filter((t) => t.status === "running").length,
    completed: displayThreads.filter((t) => t.status === "completed").length,
    failed: displayThreads.filter((t) => t.status === "failed").length,
    pending: displayThreads.filter((t) => t.status === "pending").length,
  }

  const handleThreadClick = (thread: ThreadDisplayInfo) => {
    router.push(`/chat/${thread.id}`)
  }

  return (
    <div className="h-screen bg-black flex flex-col">
      {/* Header */}
      <div className="border-b border-gray-900 bg-black px-4 py-3">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-gray-600 hover:text-gray-400 hover:bg-gray-900"
            onClick={() => router.push("/chat")}
          >
            <ArrowLeft className="h-3 w-3" />
          </Button>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-green-500 rounded-full"></div>
            <span className="text-sm text-gray-400 font-mono">All Threads</span>
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-xs text-gray-600">{filteredThreads.length} threads</span>
          </div>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="border-b border-gray-900 bg-gray-950 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-500" />
            <Input
              placeholder="Search threads..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10 bg-gray-900 border-gray-700 text-gray-300 placeholder:text-gray-600"
            />
          </div>
          <div className="flex items-center gap-1">
            <Filter className="h-4 w-4 text-gray-500" />
            <span className="text-xs text-gray-500 mr-2">Filter:</span>
            {(["all", "running", "completed", "failed", "pending"] as FilterStatus[]).map((status) => (
              <Button
                key={status}
                variant={statusFilter === status ? "secondary" : "ghost"}
                size="sm"
                className={`h-7 text-xs ${
                  statusFilter === status
                    ? "bg-gray-700 text-gray-200"
                    : "text-gray-500 hover:text-gray-300 hover:bg-gray-800"
                }`}
                onClick={() => setStatusFilter(status)}
              >
                {status === "all" ? "All" : status.charAt(0).toUpperCase() + status.slice(1)}
                <Badge variant="secondary" className="ml-1 bg-gray-800 text-gray-400 text-xs">
                  {statusCounts[status]}
                </Badge>
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        <div className="max-w-6xl mx-auto p-4">
          {statusFilter === "all" ? (
            // Show grouped view when "all" is selected
            <div className="space-y-6">
              {Object.entries(groupedThreads).map(([status, threads]) => {
                if (threads.length === 0) return null
                return (
                  <div key={status}>
                    <div className="flex items-center gap-2 mb-3">
                      <h2 className="text-base font-semibold text-gray-300 capitalize">{status} Threads</h2>
                      <Badge variant="secondary" className="bg-gray-800 text-gray-400 text-xs">
                        {threads.length}
                      </Badge>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                      {threads.map((thread) => (
                        <ThreadCard
                          key={thread.id}
                          thread={thread}
                          onClick={() => handleThreadClick(thread)}
                          getStatusColor={getStatusColor}
                          getStatusIcon={getStatusIcon}
                          getPRStatusColor={getPRStatusColor}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            // Show flat list when specific status is selected
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              {filteredThreads.map((thread) => (
                <ThreadCard
                  key={thread.id}
                  thread={thread}
                  onClick={() => handleThreadClick(thread)}
                  getStatusColor={getStatusColor}
                  getStatusIcon={getStatusIcon}
                  getPRStatusColor={getPRStatusColor}
                />
              ))}
            </div>
          )}

          {filteredThreads.length === 0 && (
            <div className="text-center py-12">
              <div className="text-gray-500 mb-2">No threads found</div>
              <div className="text-xs text-gray-600">
                {searchQuery ? "Try adjusting your search query" : "No threads match the selected filter"}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

interface ThreadCardProps {
  thread: ThreadDisplayInfo
  onClick: () => void
  getStatusColor: (status: ThreadDisplayInfo["status"]) => string
  getStatusIcon: (status: ThreadDisplayInfo["status"]) => React.ReactNode
  getPRStatusColor: (status: string) => string
}

function ThreadCard({ thread, onClick, getStatusColor, getStatusIcon, getPRStatusColor }: ThreadCardProps) {
  return (
    <Card
      className="cursor-pointer hover:shadow-lg transition-shadow bg-gray-950 border-gray-800 hover:bg-gray-900"
      onClick={onClick}
    >
      <CardHeader className="pb-2 p-3">
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <CardTitle className="text-sm font-medium truncate text-gray-300">{thread.title}</CardTitle>
            <div className="flex items-center gap-1 mt-1">
              <GitBranch className="h-2 w-2 text-gray-600" />
              <span className="text-xs text-gray-500 truncate">{thread.repository}</span>
            </div>
          </div>
          <Badge variant="secondary" className={`${getStatusColor(thread.status)} text-xs`}>
            <div className="flex items-center gap-1">
              {getStatusIcon(thread.status)}
              <span className="capitalize">{thread.status}</span>
            </div>
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="pt-0 p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-600">{thread.taskCount} tasks</span>
            <span className="text-xs text-gray-600">•</span>
            <div className="flex items-center gap-1">
              <Calendar className="h-2 w-2 text-gray-600" />
              <span className="text-xs text-gray-600">{thread.lastActivity}</span>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {thread.githubIssue && (
              <Button
                variant="ghost"
                size="sm"
                className="h-5 w-5 p-0 text-gray-500 hover:text-gray-300"
                onClick={(e) => {
                  e.stopPropagation()
                  window.open(thread.githubIssue!.url, "_blank")
                }}
              >
                <Bug className="h-3 w-3" />
              </Button>
            )}
            {thread.pullRequest && (
              <Button
                variant="ghost"
                size="sm"
                className={`h-5 w-5 p-0 hover:text-gray-300 ${getPRStatusColor(thread.pullRequest.status)}`}
                onClick={(e) => {
                  e.stopPropagation()
                  window.open(thread.pullRequest!.url, "_blank")
                }}
              >
                <GitPullRequest className="h-3 w-3" />
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
