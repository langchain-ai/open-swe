"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ArrowLeft, GitBranch, Send, User, Bot } from "lucide-react";
import { getMessageContentString } from "@open-swe/shared/messages";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ThreadSwitcher } from "./thread-switcher";
import { ThreadDisplayInfo } from "./types";
import { useStream } from "@langchain/langgraph-sdk/react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { ActionsRenderer } from "./actions-renderer";

const PROGRAMMER_ASSISTANT_ID = process.env.NEXT_PUBLIC_PROGRAMMER_ASSISTANT_ID;
const PLANNER_ASSISTANT_ID = process.env.NEXT_PUBLIC_PLANNER_ASSISTANT_ID;

interface ThreadViewProps {
  stream: ReturnType<typeof useStream<ManagerGraphState>>;
  displayThread: ThreadDisplayInfo;
  allDisplayThreads: ThreadDisplayInfo[];
  onThreadSelect: (thread: ThreadDisplayInfo) => void;
  onBackToHome: () => void;
}

export function ThreadView({
  stream,
  displayThread,
  allDisplayThreads,
  onThreadSelect,
  onBackToHome,
}: ThreadViewProps) {
  const [chatInput, setChatInput] = useState("");
  const plannerThreadId = stream.values?.plannerSession?.threadId;
  const plannerRunId = stream.values?.plannerSession?.runId;
  const [programmerSession, setProgrammerSession] =
    useState<ManagerGraphState["programmerSession"]>();

  if (!stream.messages?.length) {
    return null;
  }

  const handleSendMessage = () => {
    if (chatInput.trim()) {
      alert("SENDING MANAGER FOLLOWUPS NOT HOOKED UP YET");
      setChatInput("");
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
            {stream.messages.map((message) => (
              <div
                key={message.id}
                className="flex gap-3"
              >
                <div className="flex-shrink-0">
                  {message.type === "human" ? (
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
                      {message.type === "human" ? "You" : "AI Agent"}
                    </span>
                  </div>
                  <div className="text-sm leading-relaxed text-gray-300">
                    {getMessageContentString(message.content)}
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
            <Tabs
              defaultValue="planner"
              className="w-full"
            >
              <TabsList>
                <TabsTrigger value="planner">Planner</TabsTrigger>
                <TabsTrigger value="programmer">Programmer</TabsTrigger>
              </TabsList>
              <TabsContent value="planner">
                <Card className="border-gray-800 bg-gray-950 px-0 py-4">
                  <CardHeader>
                    <CardTitle className="text-base text-gray-300">
                      Planning Actions
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 p-3 pt-0">
                    {plannerThreadId &&
                      plannerRunId &&
                      PLANNER_ASSISTANT_ID && (
                        <ActionsRenderer<PlannerGraphState>
                          graphId={PLANNER_ASSISTANT_ID}
                          threadId={plannerThreadId}
                          runId={plannerRunId}
                          setProgrammerSession={setProgrammerSession}
                          programmerSession={programmerSession}
                        />
                      )}
                  </CardContent>
                </Card>
              </TabsContent>
              <TabsContent value="programmer">
                <Card className="border-gray-800 bg-gray-950 px-0 py-4">
                  <CardHeader>
                    <CardTitle className="text-base text-gray-300">
                      Code Actions
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 p-3 pt-0">
                    {programmerSession && PROGRAMMER_ASSISTANT_ID && (
                      <ActionsRenderer<PlannerGraphState>
                        graphId={PROGRAMMER_ASSISTANT_ID}
                        threadId={programmerSession.threadId}
                        runId={programmerSession.runId}
                      />
                    )}
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>
          </div>
        </div>
      </div>
    </div>
  );
}
