"use client";

import { v4 as uuidv4 } from "uuid";
import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ArrowLeft, GitBranch, Terminal, Clock, Loader2 } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ThreadSwitcher } from "./thread-switcher";
import { ThreadDisplayInfo } from "./types";
import { useStream } from "@langchain/langgraph-sdk/react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { ActionsRenderer } from "./actions-renderer";
import { ThemeToggle } from "../theme-toggle";
import { HumanMessage } from "@langchain/core/messages";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";
import { StickToBottom } from "use-stick-to-bottom";
import {
  StickyToBottomContent,
  ScrollToBottom,
} from "../../utils/scroll-utils";
import { ManagerChat } from "./manager-chat";
import { useCancelStream } from "@/hooks/useCancelStream";

const PROGRAMMER_ASSISTANT_ID = process.env.NEXT_PUBLIC_PROGRAMMER_ASSISTANT_ID;
const PLANNER_ASSISTANT_ID = process.env.NEXT_PUBLIC_PLANNER_ASSISTANT_ID;

interface ThreadViewProps {
  stream: ReturnType<typeof useStream<ManagerGraphState>>;
  displayThread: ThreadDisplayInfo;
  allDisplayThreads: ThreadDisplayInfo[];
  onBackToHome: () => void;
}

export function ThreadView({
  stream,
  displayThread,
  allDisplayThreads,
  onBackToHome,
}: ThreadViewProps) {
  const [chatInput, setChatInput] = useState("");
  const [selectedTab, setSelectedTab] = useState<"planner" | "programmer">(
    "planner",
  );
  const plannerThreadId = stream.values?.plannerSession?.threadId;
  const plannerRunId = stream.values?.plannerSession?.runId;
  const [programmerSession, setProgrammerSession] =
    useState<ManagerGraphState["programmerSession"]>();

  // Get planner and programmer streams with built-in reconnection
  const plannerStream = useStream<PlannerGraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    assistantId: PLANNER_ASSISTANT_ID || "",
    reconnectOnMount: true,
    threadId: plannerThreadId,
  });

  const programmerStream = useStream<GraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    assistantId: PROGRAMMER_ASSISTANT_ID || "",
    reconnectOnMount: true,
    threadId: programmerSession?.threadId,
  });

  // Join specific runs when they become available (built-in SDK method)
  useEffect(() => {
    if (plannerRunId) {
      plannerStream.joinStream(plannerRunId).catch(console.error);
    }
  }, [plannerRunId]);

  useEffect(() => {
    if (programmerSession?.runId) {
      programmerStream.joinStream(programmerSession.runId).catch(console.error);
    }
  }, [programmerSession?.runId]);

  // Cancel hooks with better error handling
  const { cancelRun: cancelPlannerRun } = useCancelStream({
    stream: plannerStream,
    threadId: plannerThreadId,
    runId: plannerRunId,
    streamName: "Planner",
  });

  const { cancelRun: cancelProgrammerRun } = useCancelStream({
    stream: programmerStream,
    threadId: programmerSession?.threadId,
    runId: programmerSession?.runId,
    streamName: "Programmer",
  });

  const handleSendMessage = () => {
    if (chatInput.trim()) {
      const newHumanMessage = new HumanMessage({
        id: uuidv4(),
        content: chatInput,
      });
      stream.submit(
        {
          messages: [newHumanMessage],
        },
        {
          streamResumable: true,
          optimisticValues: (prev) => ({
            ...prev,
            messages: [...(prev.messages ?? []), newHumanMessage],
          }),
        },
      );
      setChatInput("");
    }
  };

  const filteredMessages = stream.messages.filter((message) => {
    return !message.id?.startsWith(DO_NOT_RENDER_ID_PREFIX);
  });

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
              className={`size-2 flex-shrink-0 rounded-full ${
                displayThread.status === "running"
                  ? "bg-blue-500"
                  : displayThread.status === "completed"
                    ? "bg-green-500"
                    : "bg-red-500"
              }`}
            ></div>
            <span className="text-muted-foreground max-w-[500px] truncate font-mono text-sm">
              {displayThread.title}
            </span>
            {displayThread.repository && (
              <>
                <span className="text-muted-foreground text-xs">•</span>
                <GitBranch className="text-muted-foreground h-3 w-3" />
                <span className="text-muted-foreground truncate text-xs">
                  {displayThread.repository}
                </span>
              </>
            )}
          </div>
          <ThreadSwitcher
            currentThread={displayThread}
            allThreads={allDisplayThreads}
          />
          <ThemeToggle />
        </div>
      </div>

      {/* Main Content - Split Layout */}
      <div className="flex h-full w-full pt-12">
        <ManagerChat
          messages={filteredMessages}
          chatInput={chatInput}
          setChatInput={setChatInput}
          handleSendMessage={handleSendMessage}
          stream={stream}
        />

        {/* Right Side - Actions & Plan */}
        <div className="flex h-full flex-1 flex-col">
          <div className="relative flex-1">
            <StickToBottom
              className="absolute inset-0"
              initial={true}
            >
              <StickyToBottomContent
                className="h-full overflow-y-auto"
                contentClassName="space-y-4 p-4"
                content={
                  <Tabs
                    defaultValue="planner"
                    className="w-full"
                    value={selectedTab}
                    onValueChange={(value) =>
                      setSelectedTab(value as "planner" | "programmer")
                    }
                  >
                    <div className="flex items-center justify-between">
                      <TabsList className="bg-muted/70 dark:bg-gray-800">
                        <TabsTrigger value="planner">Planner</TabsTrigger>
                        <TabsTrigger value="programmer">Programmer</TabsTrigger>
                      </TabsList>

                      <div className="flex gap-2">
                        {selectedTab === "planner" &&
                          plannerThreadId &&
                          plannerRunId &&
                          plannerStream.isLoading && (
                            <Button
                              onClick={cancelPlannerRun}
                              size="sm"
                              variant="destructive"
                              className="h-8 px-3 text-xs"
                            >
                              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                              Stop Planner
                            </Button>
                          )}

                        {selectedTab === "programmer" &&
                          programmerSession &&
                          programmerStream.isLoading && (
                            <Button
                              onClick={cancelProgrammerRun}
                              size="sm"
                              variant="destructive"
                              className="h-8 px-3 text-xs"
                            >
                              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                              Stop Programmer
                            </Button>
                          )}
                      </div>
                    </div>

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
                                setSelectedTab={setSelectedTab}
                              />
                            )}
                          {!(
                            plannerThreadId &&
                            plannerRunId &&
                            PLANNER_ASSISTANT_ID
                          ) && (
                            <div className="flex items-center justify-center gap-2 py-8">
                              <Clock className="text-muted-foreground size-4" />
                              <span className="text-muted-foreground text-sm">
                                No planner session
                              </span>
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    </TabsContent>
                    <TabsContent value="programmer">
                      <Card className="border-border bg-card px-0 py-4 dark:bg-gray-950">
                        <CardContent className="space-y-2 p-3 pt-0">
                          {programmerSession && PROGRAMMER_ASSISTANT_ID && (
                            <ActionsRenderer<GraphState>
                              graphId={PROGRAMMER_ASSISTANT_ID}
                              threadId={programmerSession.threadId}
                              runId={programmerSession.runId}
                            />
                          )}
                          {!programmerSession && (
                            <div className="flex items-center justify-center gap-2 py-8">
                              <Terminal className="text-muted-foreground size-4" />
                              <span className="text-muted-foreground text-sm">
                                No programmer session
                              </span>
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    </TabsContent>
                  </Tabs>
                }
                footer={
                  <div className="absolute right-0 bottom-4 left-0 flex w-full justify-center">
                    <ScrollToBottom className="animate-in fade-in-0 zoom-in-95" />
                  </div>
                }
              />
            </StickToBottom>
          </div>
        </div>
      </div>
    </div>
  );
}
