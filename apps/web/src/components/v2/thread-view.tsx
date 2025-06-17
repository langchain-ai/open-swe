"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import {
  ArrowLeft,
  GitBranch,
  Edit,
  Trash2,
  Plus,
  CheckCircle,
  XCircle,
  Loader2,
  Clock,
  Terminal,
  FileText,
  Send,
  User,
  Bot,
} from "lucide-react"
import type { Thread, ActionStep } from "@/types"
import { ThreadSwitcher } from "@/components/thread-switcher"

interface ThreadDisplayInfo {
  id: string
  title: string
  repository: string
  status: "running" | "completed" | "error"
}

interface ThreadViewProps {
  thread: Thread
  displayThread: ThreadDisplayInfo
  allDisplayThreads: ThreadDisplayInfo[]
  onThreadSelect: (thread: ThreadDisplayInfo) => void
  onBackToHome: () => void
}

interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: string
}

const mockActionSteps: ActionStep[] = [
  {
    id: "1",
    title: "Preparing action",
    status: "preparing",
    type: "preparation",
  },
  {
    id: "2",
    title: "npm install @vercel/ai",
    status: "executing",
    type: "command",
    command: "npm install @vercel/ai",
    workingDirectory: "/home/user/my-project",
  },
  {
    id: "3",
    title: "npm install @vercel/ai",
    status: "completed",
    type: "command",
    command: "npm install @vercel/ai",
    workingDirectory: "/home/user/my-project",
    output: "+ @vercel/ai@3.0.0\nadded 58 packages in 2.5s",
  },
  {
    id: "4",
    title: "npm install @vercel/ai",
    status: "failed",
    type: "command",
    command: "npm instal @vercel/ai",
    workingDirectory: "/home/user/my-project",
    output:
      "npm ERR! code ENOENT\nnpm ERR! syscall spawn\nnpm ERR! path /usr/local/bin/git\nnpm ERR! errno -2\nExit code: 1",
  },
  {
    id: "5",
    title: "src/components/button.tsx",
    status: "applying",
    type: "file_edit",
    filePath: "src/components/button.tsx",
  },
  {
    id: "6",
    title: "src/components/button.tsx",
    status: "completed",
    type: "file_edit",
    filePath: "src/components/button.tsx",
    diff: "@@ -1,7 +1,8 @@\nimport React from 'react';\n\n-export const Button = ({ children }) => {\n+export const Button = ({ children }) => {",
  },
]

export function ThreadView({
  thread,
  displayThread,
  allDisplayThreads,
  onThreadSelect,
  onBackToHome,
}: ThreadViewProps) {
  const [newPlanItem, setNewPlanItem] = useState("")
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set())
  const [chatInput, setChatInput] = useState("")

  const chatMessages = thread.values.messages.map((msg, index) => ({
    id: `msg-${index}`,
    role: msg.type === "human" ? ("user" as const) : ("assistant" as const),
    content: msg.content,
    timestamp: msg.timestamp || `${index + 1} min ago`,
  }))

  const currentTask = thread.values.taskPlan.tasks[thread.values.taskPlan.activeTaskIndex]
  const currentRevision = currentTask?.planRevisions[currentTask.activeRevisionIndex]
  const planItems =
    currentRevision?.plans.map((plan) => ({
      id: `plan-${plan.index}`,
      step: plan.index + 1,
      title: plan.plan,
      status: plan.completed ? ("completed" as const) : ("proposed" as const),
      summary: plan.summary,
    })) || []

  const toggleStepExpansion = (stepId: string) => {
    const newExpanded = new Set(expandedSteps)
    if (newExpanded.has(stepId)) {
      newExpanded.delete(stepId)
    } else {
      newExpanded.add(stepId)
    }
    setExpandedSteps(newExpanded)
  }

  const handleSendMessage = () => {
    if (chatInput.trim()) {
      // Handle sending message
      console.log("Sending message:", chatInput)
      setChatInput("")
    }
  }

  const getStatusIcon = (status: ActionStep["status"]) => {
    switch (status) {
      case "preparing":
      case "executing":
      case "applying":
        return <Loader2 className="h-4 w-4 animate-spin text-blue-400" />
      case "completed":
        return <CheckCircle className="h-4 w-4 text-green-400" />
      case "failed":
        return <XCircle className="h-4 w-4 text-red-400" />
      default:
        return <Clock className="h-4 w-4 text-gray-500" />
    }
  }

  const getStepIcon = (type: ActionStep["type"]) => {
    switch (type) {
      case "command":
        return <Terminal className="h-4 w-4 text-gray-500" />
      case "file_edit":
        return <FileText className="h-4 w-4 text-gray-500" />
      default:
        return <Clock className="h-4 w-4 text-gray-500" />
    }
  }

  return (
    <div className="flex-1 flex flex-col bg-black h-screen">
      {/* Header */}
      <div className="absolute top-0 left-0 right-0 z-10 border-b border-gray-900 bg-black px-4 py-2">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-gray-600 hover:text-gray-400 hover:bg-gray-900"
            onClick={onBackToHome}
          >
            <ArrowLeft className="h-3 w-3" />
          </Button>
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <div
              className={`w-2 h-2 rounded-full ${
                displayThread.status === "running"
                  ? "bg-blue-500"
                  : displayThread.status === "completed"
                    ? "bg-green-500"
                    : "bg-red-500"
              }`}
            ></div>
            <span className="text-sm text-gray-400 font-mono truncate">{displayThread.title}</span>
            <span className="text-xs text-gray-600">•</span>
            <GitBranch className="h-3 w-3 text-gray-600" />
            <span className="text-xs text-gray-600 truncate">{displayThread.repository}</span>
          </div>
          <ThreadSwitcher
            currentThread={displayThread}
            allThreads={allDisplayThreads}
            onThreadSelect={onThreadSelect}
            onNewChat={onBackToHome}
          />
        </div>
      </div>

      {/* Main Content - Split Layout */}
      <div className="flex w-full h-full pt-12">
        {/* Left Side - Chat Interface */}
        <div className="w-1/3 border-r border-gray-900 flex flex-col bg-gray-950 h-full">
          {/* Chat Messages */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {chatMessages.map((message) => (
              <div key={message.id} className="flex gap-3">
                <div className="flex-shrink-0">
                  {message.role === "user" ? (
                    <div className="w-6 h-6 bg-gray-700 rounded-full flex items-center justify-center">
                      <User className="h-3 w-3 text-gray-400" />
                    </div>
                  ) : (
                    <div className="w-6 h-6 bg-blue-900 rounded-full flex items-center justify-center">
                      <Bot className="h-3 w-3 text-blue-400" />
                    </div>
                  )}
                </div>
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-gray-400">
                      {message.role === "user" ? "You" : "AI Agent"}
                    </span>
                    <span className="text-xs text-gray-600">{message.timestamp}</span>
                  </div>
                  <div className="text-sm text-gray-300 leading-relaxed">{message.content}</div>
                </div>
              </div>
            ))}

            {/* Add more mock messages to demonstrate scrolling */}
            {Array.from({ length: 10 }, (_, i) => (
              <div key={`extra-${i}`} className="flex gap-3">
                <div className="flex-shrink-0">
                  <div className="w-6 h-6 bg-blue-900 rounded-full flex items-center justify-center">
                    <Bot className="h-3 w-3 text-blue-400" />
                  </div>
                </div>
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-gray-400">AI Agent</span>
                    <span className="text-xs text-gray-600">{i + 3} min ago</span>
                  </div>
                  <div className="text-sm text-gray-300 leading-relaxed">
                    This is message {i + 4} to demonstrate scrolling behavior in the chat panel. Each message can
                    contain multiple lines of text and the panel should scroll independently.
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Chat Input - Fixed at bottom */}
          <div className="border-t border-gray-800 p-4 bg-gray-950">
            <div className="flex gap-2">
              <Textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Type your message..."
                className="flex-1 bg-gray-900 border-gray-700 text-gray-300 placeholder:text-gray-600 text-sm min-h-[60px] resize-none"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault()
                    handleSendMessage()
                  }
                }}
              />
              <Button
                onClick={handleSendMessage}
                disabled={!chatInput.trim()}
                size="sm"
                className="h-10 w-10 p-0 bg-gray-700 hover:bg-gray-600 self-end"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
            <div className="text-xs text-gray-600 mt-2">Press Cmd+Enter to send</div>
          </div>
        </div>

        {/* Right Side - Actions & Plan */}
        <div className="flex-1 flex flex-col h-full">
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {/* Action Steps */}
            <Card className="bg-gray-950 border-gray-800">
              <CardHeader className="p-3">
                <CardTitle className="text-base text-gray-300">Execution Steps</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 p-3 pt-0">
                {mockActionSteps.map((step) => (
                  <div key={step.id} className="border border-gray-800 rounded-lg p-3 space-y-2 bg-gray-900">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        {getStepIcon(step.type)}
                        <span className="font-medium text-sm text-gray-300">{step.title}</span>
                      </div>
                      <div className="flex items-center gap-1">
                        {step.status === "completed" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800"
                            onClick={() => toggleStepExpansion(step.id)}
                          >
                            Show summary
                          </Button>
                        )}
                        {step.status === "failed" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800"
                            onClick={() => toggleStepExpansion(step.id)}
                          >
                            Show reasoning
                          </Button>
                        )}
                        {(step.status === "preparing" || step.status === "executing" || step.status === "applying") && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800"
                            onClick={() => toggleStepExpansion(step.id)}
                          >
                            Show reasoning
                          </Button>
                        )}
                        {getStatusIcon(step.status)}
                        <span className="text-xs text-gray-500 capitalize">
                          {step.status === "applying"
                            ? "Applying patch..."
                            : step.status === "executing"
                              ? "Executing..."
                              : step.status === "preparing"
                                ? "Preparing action..."
                                : step.status === "completed"
                                  ? "Command completed"
                                  : step.status === "failed"
                                    ? "Command failed"
                                    : step.status}
                        </span>
                      </div>
                    </div>

                    {step.command && (
                      <div className="bg-black rounded p-2 font-mono text-xs">
                        <div className="text-gray-600 mb-1">{step.workingDirectory}</div>
                        <div className="text-gray-400">{step.command}</div>
                      </div>
                    )}

                    {step.output && (
                      <div className="bg-black text-green-400 rounded p-2 font-mono text-xs">
                        <pre className="whitespace-pre-wrap">{step.output}</pre>
                      </div>
                    )}

                    {step.diff && (
                      <div className="bg-black text-gray-300 rounded p-2 font-mono text-xs">
                        <pre className="whitespace-pre-wrap">{step.diff}</pre>
                      </div>
                    )}
                  </div>
                ))}

                {/* Add more mock steps to demonstrate scrolling */}
                {Array.from({ length: 5 }, (_, i) => (
                  <div key={`extra-step-${i}`} className="border border-gray-800 rounded-lg p-3 space-y-2 bg-gray-900">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Terminal className="h-4 w-4 text-gray-500" />
                        <span className="font-medium text-sm text-gray-300">Additional step {i + 7}</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <CheckCircle className="h-4 w-4 text-green-400" />
                        <span className="text-xs text-gray-500 capitalize">completed</span>
                      </div>
                    </div>
                    <div className="bg-black rounded p-2 font-mono text-xs">
                      <div className="text-gray-600 mb-1">/home/user/my-project</div>
                      <div className="text-gray-400">npm run build</div>
                    </div>
                    <div className="bg-black text-green-400 rounded p-2 font-mono text-xs">
                      <pre className="whitespace-pre-wrap">Build completed successfully</pre>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            {/* Proposed Plan */}
            <Card className="bg-gray-950 border-gray-800">
              <CardHeader className="p-3">
                <CardTitle className="text-base text-gray-300">Proposed Plan</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 p-3 pt-0">
                {planItems.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-start gap-2 p-2 border border-gray-800 rounded-lg bg-gray-900"
                  >
                    <div className="flex items-center justify-center w-5 h-5 rounded-full bg-gray-800 text-xs font-medium text-gray-400 mt-0.5">
                      {item.step}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-gray-400">{item.title}</p>
                      <Badge variant="secondary" className="mt-1 text-xs bg-gray-800 text-gray-400">
                        {item.status}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-0.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0 text-gray-500 hover:text-gray-300 hover:bg-gray-800"
                      >
                        <Edit className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0 text-gray-500 hover:text-gray-300 hover:bg-gray-800"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </div>
                ))}

                {/* Add more mock plan items to demonstrate scrolling */}
                {Array.from({ length: 8 }, (_, i) => (
                  <div
                    key={`extra-plan-${i}`}
                    className="flex items-start gap-2 p-2 border border-gray-800 rounded-lg bg-gray-900"
                  >
                    <div className="flex items-center justify-center w-5 h-5 rounded-full bg-gray-800 text-xs font-medium text-gray-400 mt-0.5">
                      {i + 11}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-gray-400">
                        Additional plan item {i + 11}: Implement feature or fix issue
                      </p>
                      <Badge variant="secondary" className="mt-1 text-xs bg-gray-800 text-gray-400">
                        proposed
                      </Badge>
                    </div>
                    <div className="flex items-center gap-0.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0 text-gray-500 hover:text-gray-300 hover:bg-gray-800"
                      >
                        <Edit className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0 text-gray-500 hover:text-gray-300 hover:bg-gray-800"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </div>
                ))}

                <div className="border-2 border-dashed border-gray-700 rounded-lg p-3">
                  <Textarea
                    placeholder="Add new plan item"
                    value={newPlanItem}
                    onChange={(e) => setNewPlanItem(e.target.value)}
                    className="mb-2 text-xs min-h-[60px] bg-black border-gray-800 text-gray-400 placeholder:text-gray-600"
                  />
                  <Button size="sm" className="h-7 text-xs bg-gray-800 hover:bg-gray-700 text-gray-300">
                    <Plus className="h-3 w-3 mr-1" />
                    Add
                  </Button>
                </div>

                <div className="flex gap-2 pt-3">
                  <Button className="flex-1 bg-teal-800 hover:bg-teal-700 h-8 text-xs text-gray-200">Approve</Button>
                  <Button
                    variant="destructive"
                    className="flex-1 h-8 text-xs bg-red-900 hover:bg-red-800 text-gray-200"
                  >
                    Reject
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </div>
  )
}
