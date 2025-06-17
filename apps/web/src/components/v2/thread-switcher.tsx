"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { CheckCircle, XCircle, Loader2, GitBranch, Layers3, Plus, Bug, GitPullRequest } from "lucide-react"
import type { Thread } from "@/types"
import { useRouter } from "next/navigation"

interface ThreadSwitcherProps {
  currentThread: Thread
  allThreads: Thread[]
  onThreadSelect: (thread: Thread) => void
  onNewChat: () => void
}

export function ThreadSwitcher({ currentThread, allThreads, onThreadSelect, onNewChat }: ThreadSwitcherProps) {
  const [open, setOpen] = useState(false)
  const router = useRouter()

  const getStatusIcon = (status: Thread["status"]) => {
    switch (status) {
      case "running":
        return <Loader2 className="h-3 w-3 animate-spin text-blue-400" />
      case "completed":
        return <CheckCircle className="h-3 w-3 text-green-400" />
      case "failed":
        return <XCircle className="h-3 w-3 text-red-400" />
      default:
        return <div className="h-3 w-3 rounded-full bg-gray-700" />
    }
  }

  const getStatusColor = (status: Thread["status"]) => {
    switch (status) {
      case "running":
        return "bg-blue-950 text-blue-400"
      case "completed":
        return "bg-green-950 text-green-400"
      case "failed":
        return "bg-red-950 text-red-400"
      default:
        return "bg-gray-800 text-gray-400"
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

  const otherThreads = allThreads.filter((t) => t.id !== currentThread.id)
  const runningCount = otherThreads.filter((t) => t.status === "running").length

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs gap-1 bg-gray-900 border-gray-700 text-gray-400 hover:bg-gray-800 hover:text-gray-300"
        >
          <Layers3 className="h-3 w-3" />
          <span className="hidden sm:inline">Switch Thread</span>
          {runningCount > 0 && (
            <Badge variant="secondary" className="bg-blue-950 text-blue-400 text-xs h-4 px-1">
              {runningCount}
            </Badge>
          )}
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-80 sm:w-96 bg-gray-950 border-gray-800">
        <SheetHeader className="pb-4">
          <SheetTitle className="text-base text-gray-300">All Threads</SheetTitle>
        </SheetHeader>

        <div className="space-y-3">
          {/* New Chat Button */}
          <Button
            onClick={() => {
              router.push("/chat")
              setOpen(false)
            }}
            className="w-full justify-start gap-2 h-8 text-xs bg-gray-900 hover:bg-gray-800 text-gray-300 border-gray-700"
            variant="outline"
          >
            <Plus className="h-3 w-3" />
            Start New Chat
          </Button>

          {/* Current Thread */}
          <div className="space-y-2">
            <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide">Current Thread</h3>
            <div className="p-3 border-2 border-blue-800 bg-blue-950 rounded-lg">
              <div className="flex items-start gap-2">
                {getStatusIcon(currentThread.status)}
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium text-gray-300 truncate">{currentThread.title}</div>
                  <div className="flex items-center gap-1 mt-1">
                    <GitBranch className="h-2 w-2 text-gray-600" />
                    <span className="text-xs text-gray-500 truncate">{currentThread.repository}</span>
                    <span className="text-xs text-gray-700">•</span>
                    <span className="text-xs text-gray-500">{currentThread.lastActivity}</span>
                  </div>
                  <div className="flex items-center justify-between mt-2">
                    <Badge variant="secondary" className={`${getStatusColor(currentThread.status)} text-xs`}>
                      {currentThread.status}
                    </Badge>
                    <div className="flex items-center gap-1">
                      {currentThread.githubIssue && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-5 w-5 p-0 text-gray-500 hover:text-gray-300"
                          onClick={(e) => {
                            e.stopPropagation()
                            window.open(currentThread.githubIssue!.url, "_blank")
                          }}
                        >
                          <Bug className="h-3 w-3" />
                        </Button>
                      )}
                      {currentThread.pullRequest && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className={`h-5 w-5 p-0 hover:text-gray-300 ${getPRStatusColor(currentThread.pullRequest.status)}`}
                          onClick={(e) => {
                            e.stopPropagation()
                            window.open(currentThread.pullRequest!.url, "_blank")
                          }}
                        >
                          <GitPullRequest className="h-3 w-3" />
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Other Threads */}
          {otherThreads.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide">Other Threads</h3>
              <ScrollArea className="h-96">
                <div className="space-y-1">
                  {otherThreads.map((thread) => (
                    <Button
                      key={thread.id}
                      variant="ghost"
                      className="w-full justify-start p-3 h-auto text-left hover:bg-gray-800 text-gray-400"
                      onClick={() => {
                        router.push(`/chat/${thread.id}`)
                        setOpen(false)
                      }}
                    >
                      <div className="flex items-start gap-2 w-full">
                        {getStatusIcon(thread.status)}
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-medium text-gray-300 truncate">{thread.title}</div>
                          <div className="flex items-center gap-1 mt-1">
                            <GitBranch className="h-2 w-2 text-gray-600" />
                            <span className="text-xs text-gray-500 truncate">{thread.repository}</span>
                            <span className="text-xs text-gray-700">•</span>
                            <span className="text-xs text-gray-500">{thread.lastActivity}</span>
                          </div>
                          <div className="flex items-center justify-between mt-1">
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-gray-600">{thread.taskCount} tasks</span>
                              <Badge variant="secondary" className={`${getStatusColor(thread.status)} text-xs`}>
                                {thread.status}
                              </Badge>
                            </div>
                            <div className="flex items-center gap-1">
                              {thread.githubIssue && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-4 w-4 p-0 text-gray-600 hover:text-gray-400"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    window.open(thread.githubIssue!.url, "_blank")
                                  }}
                                >
                                  <Bug className="h-2 w-2" />
                                </Button>
                              )}
                              {thread.pullRequest && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className={`h-4 w-4 p-0 hover:text-gray-400 ${getPRStatusColor(thread.pullRequest.status)}`}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    window.open(thread.pullRequest!.url, "_blank")
                                  }}
                                >
                                  <GitPullRequest className="h-2 w-2" />
                                </Button>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    </Button>
                  ))}
                </div>
              </ScrollArea>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
