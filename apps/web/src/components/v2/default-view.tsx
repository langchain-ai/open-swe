"use client"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Camera, Upload, FileText, CheckCircle, XCircle, Loader2, GitBranch, GitPullRequest, Bug } from "lucide-react"
import type { ThreadDisplayInfo } from "@/types"
import { TerminalInput } from "@/components/terminal-input"
import { useRouter } from "next/navigation"

interface DefaultViewProps {
  threads: ThreadDisplayInfo[]
  onThreadSelect: (thread: ThreadDisplayInfo) => void
}

export function DefaultView({ threads, onThreadSelect }: DefaultViewProps) {
  const router = useRouter()

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

  return (
    <div className="flex-1 flex flex-col">
      {/* Header */}
      <div className="border-b border-gray-900 bg-black px-4 py-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 bg-green-500 rounded-full"></div>
            <span className="text-sm text-gray-400 font-mono">ai-agent</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-600">ready</span>
            <div className="w-1 h-1 bg-gray-600 rounded-full"></div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-auto">
        <div className="max-w-4xl mx-auto p-4 space-y-6">
          {/* Terminal Chat Input */}
          <Card className="bg-gray-950 border-gray-800">
            <CardContent className="p-4">
              <div className="space-y-3">
                <TerminalInput
                  onSend={(message, repo, branch) => {
                    // In a real app, this would create a new thread and redirect
                    console.log("Creating new thread with:", message, "to", `${repo.owner}/${repo.name}:${branch}`)
                    // For demo purposes, redirect to thread 1
                    router.push("/chat/1")
                  }}
                  placeholder="Describe your coding task or ask a question..."
                />
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs bg-gray-900 border-gray-700 text-gray-400 hover:bg-gray-800 hover:text-gray-300"
                  >
                    <Camera className="h-3 w-3 mr-1" />
                    Screenshot
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs bg-gray-900 border-gray-700 text-gray-400 hover:bg-gray-800 hover:text-gray-300"
                  >
                    <Upload className="h-3 w-3 mr-1" />
                    Upload File
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs bg-gray-900 border-gray-700 text-gray-400 hover:bg-gray-800 hover:text-gray-300"
                  >
                    <FileText className="h-3 w-3 mr-1" />
                    Import Project
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Recent & Running Threads */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-base font-semibold text-gray-300">Recent & Running Threads</h2>
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs bg-gray-900 border-gray-700 text-gray-400 hover:bg-gray-800 hover:text-gray-300"
                onClick={() => router.push("/chat/threads")}
              >
                View All
              </Button>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              {threads.slice(0, 4).map((thread) => (
                <Card
                  key={thread.id}
                  className="cursor-pointer hover:shadow-lg transition-shadow bg-gray-950 border-gray-800 hover:bg-gray-900"
                  onClick={() => onThreadSelect(thread)}
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
                        <span className="text-xs text-gray-600">{thread.lastActivity}</span>
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
              ))}
            </div>
          </div>

          {/* Quick Actions */}
          <div>
            <h2 className="text-base font-semibold text-gray-300 mb-3">Quick Actions</h2>
            <div className="grid gap-3 md:grid-cols-3">
              <Card className="cursor-pointer hover:shadow-lg transition-shadow bg-gray-950 border-gray-800 hover:bg-gray-900">
                <CardHeader className="p-3">
                  <CardTitle className="text-sm text-gray-300">Debug Code</CardTitle>
                  <CardDescription className="text-xs text-gray-500">
                    Find and fix issues in your codebase
                  </CardDescription>
                </CardHeader>
              </Card>
              <Card className="cursor-pointer hover:shadow-lg transition-shadow bg-gray-950 border-gray-800 hover:bg-gray-900">
                <CardHeader className="p-3">
                  <CardTitle className="text-sm text-gray-300">Add Feature</CardTitle>
                  <CardDescription className="text-xs text-gray-500">Implement new functionality</CardDescription>
                </CardHeader>
              </Card>
              <Card className="cursor-pointer hover:shadow-lg transition-shadow bg-gray-950 border-gray-800 hover:bg-gray-900">
                <CardHeader className="p-3">
                  <CardTitle className="text-sm text-gray-300">Refactor Code</CardTitle>
                  <CardDescription className="text-xs text-gray-500">
                    Improve code structure and performance
                  </CardDescription>
                </CardHeader>
              </Card>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
