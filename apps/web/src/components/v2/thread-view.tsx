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
    <div className="bg-background flex h-screen flex-1 flex-col">
      {/* Header */}
      <div className="border-border bg-card absolute top-0 right-0 left-0 z-10 border-b px-4 py-2">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:bg-muted hover:text-foreground h-6 w-6 p-0"
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
            <span className="text-muted-foreground truncate font-mono text-sm">
              {displayThread.title}
            </span>
            <span className="text-muted-foreground text-xs">•</span>
            <GitBranch className="text-muted-foreground h-3 w-3" />
            <span className="text-muted-foreground truncate text-xs">
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
        <div className="border-border bg-muted/30 flex h-full w-1/3 flex-col border-r dark:bg-gray-950">
          {/* Chat Messages */}
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {stream.messages.map((message) => (
              <div
                key={message.id}
                className="flex gap-3"
              >
                <div className="flex-shrink-0">
                  {message.type === "human" ? (
                    <div className="bg-muted flex h-6 w-6 items-center justify-center rounded-full dark:bg-gray-700">
                      <User className="text-muted-foreground h-3 w-3" />
                    </div>
                  ) : (
                    <div className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-100 dark:bg-blue-900">
                      <Bot className="h-3 w-3 text-blue-700 dark:text-blue-400" />
                    </div>
                  )}
                </div>
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-muted-foreground text-xs font-medium">
                      {message.type === "human" ? "You" : "Agent"}
                    </span>
                  </div>
                  <div className="text-foreground text-sm leading-relaxed">
                    {getMessageContentString(message.content)}
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Chat Input - Fixed at bottom */}
          <div className="border-border bg-muted/30 border-t p-4 dark:bg-gray-950">
            <div className="flex gap-2">
              <Textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Type your message..."
                className="border-border bg-background text-foreground placeholder:text-muted-foreground min-h-[60px] flex-1 resize-none text-sm dark:bg-gray-900"
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
                className="bg-muted hover:bg-muted/80 h-10 w-10 self-end p-0 dark:bg-gray-700 hover:dark:bg-gray-600"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
            <div className="text-muted-foreground mt-2 text-xs">
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
              <TabsList className="bg-muted/70 dark:bg-gray-800">
                <TabsTrigger value="planner">Planner</TabsTrigger>
                <TabsTrigger value="programmer">Programmer</TabsTrigger>
              </TabsList>
              <TabsContent value="planner">
                <Card className="border-border bg-card px-0 py-4 dark:bg-gray-950">
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
                <Card className="border-border bg-card px-0 py-4 dark:bg-gray-950">
                  <CardHeader>
                    <CardTitle className="text-foreground text-base">
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
