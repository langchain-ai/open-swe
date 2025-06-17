"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
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
} from "lucide-react";
import { Message, Thread } from "@langchain/langgraph-sdk";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getMessageContentString } from "@open-swe/shared/messages";
import { isHumanMessageSDK } from "@/lib/langchain-messages";
import { ThreadSwitcher } from "./thread-switcher";

interface ThreadDisplayInfo {
  id: string;
  title: string;
  repository: string;
  status: "running" | "completed" | "error";
}

interface ThreadViewProps {
  thread: Thread<GraphState>;
  displayThread: ThreadDisplayInfo;
  allDisplayThreads: ThreadDisplayInfo[];
  onThreadSelect: (thread: ThreadDisplayInfo) => void;
  onBackToHome: () => void;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

const mockActionSteps = [
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
];

export function ThreadView({
  thread,
  displayThread,
  allDisplayThreads,
  onThreadSelect,
  onBackToHome,
}: ThreadViewProps) {
  const [newPlanItem, setNewPlanItem] = useState("");
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [chatInput, setChatInput] = useState("");

  const chatMessages = thread.values.messages.map((msg, index) => ({
    id: `msg-${index}`,
    // TODO: Fix this. we should be using `stream.messages` instead
    role: isHumanMessageSDK(msg as unknown as Message)
      ? ("user" as const)
      : ("assistant" as const),
    content: msg.content,
  }));

  const currentTask =
    thread.values.taskPlan.tasks[thread.values.taskPlan.activeTaskIndex];
  const currentRevision =
    currentTask?.planRevisions[currentTask.activeRevisionIndex];
  const planItems =
    currentRevision?.plans.map((plan) => ({
      id: `plan-${plan.index}`,
      step: plan.index + 1,
      title: plan.plan,
      status: plan.completed ? ("completed" as const) : ("proposed" as const),
      summary: plan.summary,
    })) || [];

  const toggleStepExpansion = (stepId: string) => {
    const newExpanded = new Set(expandedSteps);
    if (newExpanded.has(stepId)) {
      newExpanded.delete(stepId);
    } else {
      newExpanded.add(stepId);
    }
    setExpandedSteps(newExpanded);
  };

  const handleSendMessage = () => {
    if (chatInput.trim()) {
      // Handle sending message
      console.log("Sending message:", chatInput);
      setChatInput("");
    }
  };

  // TODO: Replace with actual status
  const getStatusIcon = (status: string) => {
    switch (status) {
      case "preparing":
      case "executing":
      case "applying":
        return <Loader2 className="h-4 w-4 animate-spin text-blue-400" />;
      case "completed":
        return <CheckCircle className="h-4 w-4 text-green-400" />;
      case "failed":
        return <XCircle className="h-4 w-4 text-red-400" />;
      default:
        return <Clock className="h-4 w-4 text-gray-500" />;
    }
  };

  // TODO: Replace with actual type
  const getStepIcon = (type: string) => {
    switch (type) {
      case "command":
        return <Terminal className="h-4 w-4 text-gray-500" />;
      case "file_edit":
        return <FileText className="h-4 w-4 text-gray-500" />;
      default:
        return <Clock className="h-4 w-4 text-gray-500" />;
    }
  };

  return (
    <div className="flex h-screen flex-1 flex-col bg-black">
      {/* Header */}
      <div className="absolute top-0 right-0 left-0 z-10 border-b border-gray-900 bg-black px-4 py-2">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-gray-600 hover:bg-gray-900 hover:text-gray-400"
            onClick={onBackToHome}
          >
            <ArrowLeft className="h-3 w-3" />
          </Button>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <div
              className={`h-2 w-2 rounded-full ${
                displayThread.status === "running"
                  ? "bg-blue-500"
                  : displayThread.status === "completed"
                    ? "bg-green-500"
                    : "bg-red-500"
              }`}
            ></div>
            <span className="truncate font-mono text-sm text-gray-400">
              {displayThread.title}
            </span>
            <span className="text-xs text-gray-600">•</span>
            <GitBranch className="h-3 w-3 text-gray-600" />
            <span className="truncate text-xs text-gray-600">
              {displayThread.repository}
            </span>
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
      <div className="flex h-full w-full pt-12">
        {/* Left Side - Chat Interface */}
        <div className="flex h-full w-1/3 flex-col border-r border-gray-900 bg-gray-950">
          {/* Chat Messages */}
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {chatMessages.map((message) => (
              <div
                key={message.id}
                className="flex gap-3"
              >
                <div className="flex-shrink-0">
                  {message.role === "user" ? (
                    <div className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-700">
                      <User className="h-3 w-3 text-gray-400" />
                    </div>
                  ) : (
                    <div className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-900">
                      <Bot className="h-3 w-3 text-blue-400" />
                    </div>
                  )}
                </div>
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-gray-400">
                      {message.role === "user" ? "You" : "AI Agent"}
                    </span>
                  </div>
                  <div className="text-sm leading-relaxed text-gray-300">
                    {getMessageContentString(message.content)}
                  </div>
                </div>
              </div>
            ))}

            {/* Add more mock messages to demonstrate scrolling */}
            {Array.from({ length: 10 }, (_, i) => (
              <div
                key={`extra-${i}`}
                className="flex gap-3"
              >
                <div className="flex-shrink-0">
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-900">
                    <Bot className="h-3 w-3 text-blue-400" />
                  </div>
                </div>
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-gray-400">
                      AI Agent
                    </span>
                    <span className="text-xs text-gray-600">
                      {i + 3} min ago
                    </span>
                  </div>
                  <div className="text-sm leading-relaxed text-gray-300">
                    This is message {i + 4} to demonstrate scrolling behavior in
                    the chat panel. Each message can contain multiple lines of
                    text and the panel should scroll independently.
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Chat Input - Fixed at bottom */}
          <div className="border-t border-gray-800 bg-gray-950 p-4">
            <div className="flex gap-2">
              <Textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Type your message..."
                className="min-h-[60px] flex-1 resize-none border-gray-700 bg-gray-900 text-sm text-gray-300 placeholder:text-gray-600"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    handleSendMessage();
                  }
                }}
              />
              <Button
                onClick={handleSendMessage}
                disabled={!chatInput.trim()}
                size="sm"
                className="h-10 w-10 self-end bg-gray-700 p-0 hover:bg-gray-600"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
            <div className="mt-2 text-xs text-gray-600">
              Press Cmd+Enter to send
            </div>
          </div>
        </div>

        {/* Right Side - Actions & Plan */}
        <div className="flex h-full flex-1 flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {/* Action Steps */}
            <Card className="border-gray-800 bg-gray-950">
              <CardHeader className="p-3">
                <CardTitle className="text-base text-gray-300">
                  Execution Steps
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 p-3 pt-0">
                {mockActionSteps.map((step) => (
                  <div
                    key={step.id}
                    className="space-y-2 rounded-lg border border-gray-800 bg-gray-900 p-3"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        {getStepIcon(step.type)}
                        <span className="text-sm font-medium text-gray-300">
                          {step.title}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        {step.status === "completed" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 text-xs text-gray-500 hover:bg-gray-800 hover:text-gray-300"
                            onClick={() => toggleStepExpansion(step.id)}
                          >
                            Show summary
                          </Button>
                        )}
                        {step.status === "failed" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 text-xs text-gray-500 hover:bg-gray-800 hover:text-gray-300"
                            onClick={() => toggleStepExpansion(step.id)}
                          >
                            Show reasoning
                          </Button>
                        )}
                        {(step.status === "preparing" ||
                          step.status === "executing" ||
                          step.status === "applying") && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 text-xs text-gray-500 hover:bg-gray-800 hover:text-gray-300"
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
                      <div className="rounded bg-black p-2 font-mono text-xs">
                        <div className="mb-1 text-gray-600">
                          {step.workingDirectory}
                        </div>
                        <div className="text-gray-400">{step.command}</div>
                      </div>
                    )}

                    {step.output && (
                      <div className="rounded bg-black p-2 font-mono text-xs text-green-400">
                        <pre className="whitespace-pre-wrap">{step.output}</pre>
                      </div>
                    )}

                    {step.diff && (
                      <div className="rounded bg-black p-2 font-mono text-xs text-gray-300">
                        <pre className="whitespace-pre-wrap">{step.diff}</pre>
                      </div>
                    )}
                  </div>
                ))}

                {/* Add more mock steps to demonstrate scrolling */}
                {Array.from({ length: 5 }, (_, i) => (
                  <div
                    key={`extra-step-${i}`}
                    className="space-y-2 rounded-lg border border-gray-800 bg-gray-900 p-3"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Terminal className="h-4 w-4 text-gray-500" />
                        <span className="text-sm font-medium text-gray-300">
                          Additional step {i + 7}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        <CheckCircle className="h-4 w-4 text-green-400" />
                        <span className="text-xs text-gray-500 capitalize">
                          completed
                        </span>
                      </div>
                    </div>
                    <div className="rounded bg-black p-2 font-mono text-xs">
                      <div className="mb-1 text-gray-600">
                        /home/user/my-project
                      </div>
                      <div className="text-gray-400">npm run build</div>
                    </div>
                    <div className="rounded bg-black p-2 font-mono text-xs text-green-400">
                      <pre className="whitespace-pre-wrap">
                        Build completed successfully
                      </pre>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            {/* Proposed Plan */}
            <Card className="border-gray-800 bg-gray-950">
              <CardHeader className="p-3">
                <CardTitle className="text-base text-gray-300">
                  Proposed Plan
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 p-3 pt-0">
                {planItems.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-start gap-2 rounded-lg border border-gray-800 bg-gray-900 p-2"
                  >
                    <div className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-gray-800 text-xs font-medium text-gray-400">
                      {item.step}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs text-gray-400">{item.title}</p>
                      <Badge
                        variant="secondary"
                        className="mt-1 bg-gray-800 text-xs text-gray-400"
                      >
                        {item.status}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-0.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0 text-gray-500 hover:bg-gray-800 hover:text-gray-300"
                      >
                        <Edit className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0 text-gray-500 hover:bg-gray-800 hover:text-gray-300"
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
                    className="flex items-start gap-2 rounded-lg border border-gray-800 bg-gray-900 p-2"
                  >
                    <div className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-gray-800 text-xs font-medium text-gray-400">
                      {i + 11}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs text-gray-400">
                        Additional plan item {i + 11}: Implement feature or fix
                        issue
                      </p>
                      <Badge
                        variant="secondary"
                        className="mt-1 bg-gray-800 text-xs text-gray-400"
                      >
                        proposed
                      </Badge>
                    </div>
                    <div className="flex items-center gap-0.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0 text-gray-500 hover:bg-gray-800 hover:text-gray-300"
                      >
                        <Edit className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0 text-gray-500 hover:bg-gray-800 hover:text-gray-300"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </div>
                ))}

                <div className="rounded-lg border-2 border-dashed border-gray-700 p-3">
                  <Textarea
                    placeholder="Add new plan item"
                    value={newPlanItem}
                    onChange={(e) => setNewPlanItem(e.target.value)}
                    className="mb-2 min-h-[60px] border-gray-800 bg-black text-xs text-gray-400 placeholder:text-gray-600"
                  />
                  <Button
                    size="sm"
                    className="h-7 bg-gray-800 text-xs text-gray-300 hover:bg-gray-700"
                  >
                    <Plus className="mr-1 h-3 w-3" />
                    Add
                  </Button>
                </div>

                <div className="flex gap-2 pt-3">
                  <Button className="h-8 flex-1 bg-teal-800 text-xs text-gray-200 hover:bg-teal-700">
                    Approve
                  </Button>
                  <Button
                    variant="destructive"
                    className="h-8 flex-1 bg-red-900 text-xs text-gray-200 hover:bg-red-800"
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
  );
}
