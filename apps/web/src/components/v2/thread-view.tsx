"use client";

import { v4 as uuidv4 } from "uuid";
import { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ArrowLeft, GitBranch, Terminal, Clock } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ThreadSwitcher } from "./thread-switcher";
import { ThreadMetadata } from "./types";
import { useStream } from "@langchain/langgraph-sdk/react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import {
  GraphState,
  CacheMetrics,
  ModelTokenData,
} from "@open-swe/shared/open-swe/types";
import { ActionsRenderer } from "./actions-renderer";
import { ThemeToggle } from "../theme-toggle";
import {
  coerceMessageLikeToMessage,
  HumanMessage,
} from "@langchain/core/messages";
import {
  DO_NOT_RENDER_ID_PREFIX,
  PROGRAMMER_GRAPH_ID,
  PLANNER_GRAPH_ID,
} from "@open-swe/shared/constants";
import { useThreadStatus } from "@/hooks/useThreadStatus";
import { cn } from "@/lib/utils";

import {
  StickyToBottomContent,
  ScrollToBottom,
} from "../../utils/scroll-utils";
import { ManagerChat } from "./manager-chat";
import { CancelStreamButton } from "./cancel-stream-button";
import { ProgressBar } from "@/components/tasks/progress-bar";
import { TasksSidebar } from "@/components/tasks";
import { TaskPlan } from "@open-swe/shared/open-swe/types";
import { ErrorState } from "./types";
import {
  CustomNodeEvent,
  isCustomNodeEvent,
} from "@open-swe/shared/open-swe/custom-node-events";
import { StickToBottom } from "use-stick-to-bottom";
import { TokenUsage } from "./token-usage";
import { HumanMessage as HumanMessageSDK } from "@langchain/langgraph-sdk";
import { getMessageContentString } from "@open-swe/shared/messages";
import { useUser } from "@/hooks/useUser";

interface ThreadViewProps {
  stream: ReturnType<typeof useStream<ManagerGraphState>>;
  displayThread: ThreadMetadata;
  onBackToHome: () => void;
}

const joinTokenData = (
  plannerTokenData?: CacheMetrics | ModelTokenData[],
  programmerTokenData?: CacheMetrics | ModelTokenData[],
): ModelTokenData[] | CacheMetrics[] => {
  if (!plannerTokenData && !programmerTokenData) {
    return [];
  }
  if (plannerTokenData && programmerTokenData) {
    return [
      ...(Array.isArray(plannerTokenData)
        ? plannerTokenData
        : [plannerTokenData]),
      ...(Array.isArray(programmerTokenData)
        ? programmerTokenData
        : [programmerTokenData]),
    ];
  }

  if (plannerTokenData && !programmerTokenData) {
    return Array.isArray(plannerTokenData)
      ? plannerTokenData
      : [plannerTokenData];
  }

  if (!plannerTokenData && programmerTokenData) {
    return Array.isArray(programmerTokenData)
      ? programmerTokenData
      : [programmerTokenData];
  }

  return [];
};

export function ThreadView({
  stream,
  displayThread,
  onBackToHome,
}: ThreadViewProps) {
  const { user } = useUser();
  const [chatInput, setChatInput] = useState("");
  const [selectedTab, setSelectedTab] = useState<"planner" | "programmer">(
    "planner",
  );
  const [plannerSession, setPlannerSession] =
    useState<ManagerGraphState["plannerSession"]>();
  const [programmerSession, setProgrammerSession] =
    useState<ManagerGraphState["programmerSession"]>();
  const [isTaskSidebarOpen, setIsTaskSidebarOpen] = useState(false);
  const [programmerTaskPlan, setProgrammerTaskPlan] = useState<TaskPlan>();
  const [optimisticMessage, setOptimisticMessage] =
    useState<HumanMessageSDK | null>(null);

  const { status: realTimeStatus, taskPlan: realTimeTaskPlan } =
    useThreadStatus(displayThread.id, {
      useTaskPlanConfig: true,
    });

  const [errorState, setErrorState] = useState<ErrorState | null>(null);

  // Load optimistic message from sessionStorage
  useEffect(() => {
    try {
      const storedData = sessionStorage.getItem(
        `lg:initial-message:${displayThread.id}`,
      );
      if (storedData) {
        const { message: stringifiedMessage } = JSON.parse(storedData);
        const message = coerceMessageLikeToMessage(stringifiedMessage);
        const reconstructedMessage: HumanMessageSDK = {
          type: "human",
          id: message.id,
          content: getMessageContentString(message.content),
        };
        setOptimisticMessage(reconstructedMessage);
      }
    } catch (error) {
      console.error(
        "Failed to load optimistic message from sessionStorage:",
        error,
      );
    }
  }, [displayThread.id, stream.messages.length]);

  // If there's more than 1 message, we've received both the human and ai message, so we can remove the optimistic message
  useEffect(() => {
    if (stream.messages.length > 1 && optimisticMessage) {
      setOptimisticMessage(null);
      if (displayThread.id) {
        try {
          sessionStorage.removeItem(`lg:initial-message:${displayThread.id}`);
        } catch (error) {
          console.error(
            "Failed to remove optimistic message from sessionStorage:",
            error,
          );
        }
      }
    }
  }, [stream.messages, optimisticMessage, displayThread.id]);

  // Clean up sessionStorage on unmount
  useEffect(() => {
    return () => {
      if (displayThread.id) {
        try {
          sessionStorage.removeItem(`lg:initial-message:${displayThread.id}`);
        } catch {
          // no-op
        }
      }
    };
  }, [displayThread.id]);

  const [customPlannerNodeEvents, setCustomPlannerNodeEvents] = useState<
    CustomNodeEvent[]
  >([]);
  const [customProgrammerNodeEvents, setCustomProgrammerNodeEvents] = useState<
    CustomNodeEvent[]
  >([]);

  const plannerStream = useStream<PlannerGraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    assistantId: PLANNER_GRAPH_ID,
    reconnectOnMount: true,
    threadId: plannerSession?.threadId,
    onCustomEvent: (event) => {
      if (isCustomNodeEvent(event)) {
        setCustomPlannerNodeEvents((prev) => [...prev, event]);
      }
    },
    fetchStateHistory: false,
  });

  const joinedPlannerRunId = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (
      plannerSession?.runId &&
      plannerSession.runId !== joinedPlannerRunId.current
    ) {
      joinedPlannerRunId.current = plannerSession.runId;
      plannerStream.joinStream(plannerSession.runId).catch(console.error);
    } else if (!plannerSession?.runId) {
      joinedPlannerRunId.current = undefined;
    }
  }, [plannerSession]);

  const programmerStream = useStream<GraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    assistantId: PROGRAMMER_GRAPH_ID,
    reconnectOnMount: true,
    threadId: programmerSession?.threadId,
    onCustomEvent: (event) => {
      if (isCustomNodeEvent(event)) {
        setCustomProgrammerNodeEvents((prev) => [...prev, event]);
      }
    },
    fetchStateHistory: false,
  });

  const joinedProgrammerRunId = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (
      programmerSession?.runId &&
      programmerSession.runId !== joinedProgrammerRunId.current
    ) {
      joinedProgrammerRunId.current = programmerSession.runId;
      programmerStream.joinStream(programmerSession.runId).catch(console.error);
    } else if (!programmerSession?.runId) {
      joinedProgrammerRunId.current = undefined;
    }
  }, [programmerSession]);

  useEffect(() => {
    if (
      stream?.values?.plannerSession &&
      plannerSession?.runId !== stream.values.plannerSession.runId
    ) {
      const prevPlannerSession = plannerSession;
      setPlannerSession(stream.values.plannerSession);

      if (prevPlannerSession && selectedTab === "programmer") {
        setSelectedTab("planner");
      }
    }
  }, [stream?.values]);

  useEffect(() => {
    if (stream.error) {
      const rawErrorMessage =
        typeof stream.error === "object" && "message" in stream.error
          ? (stream.error.message as string)
          : "An unknown error occurred in the manager";

      if (rawErrorMessage.includes("overloaded_error")) {
        setErrorState({
          message:
            "An Anthropic overloaded error occurred. This error occurs when Anthropic APIs experience high traffic across all users.",
          details: rawErrorMessage,
        });
      } else {
        setErrorState({
          message: rawErrorMessage,
        });
      }
    } else {
      setErrorState(null);
    }
  }, [stream.error]);

  useEffect(() => {
    if (
      plannerStream.values.programmerSession &&
      (plannerStream.values.programmerSession.runId !==
        programmerSession?.runId ||
        plannerStream.values.programmerSession.threadId !==
          programmerSession?.threadId)
    ) {
      setProgrammerSession?.(plannerStream.values.programmerSession);

      // Only switch tabs from the planner ActionsRenderer to ensure proper timing
      // This allows the accepted plan step to be visible before switching
      if (selectedTab === PLANNER_GRAPH_ID) {
        // Add a small delay to allow the accepted plan step to render first
        setTimeout(() => {
          setSelectedTab?.("programmer");
        }, 2000);
      }
    }
  }, [plannerStream.values, selectedTab]);

  useEffect(() => {
    if (programmerStream.values?.taskPlan) {
      setProgrammerTaskPlan(programmerStream.values.taskPlan);
    } else if (realTimeTaskPlan) {
      setProgrammerTaskPlan(realTimeTaskPlan);
    }
  }, [programmerStream.values, realTimeTaskPlan]);

  const getStatusDotColor = (status: string) => {
    switch (status) {
      case "running":
        return "bg-blue-500 dark:bg-blue-400";
      case "completed":
        return "bg-green-500 dark:bg-green-400";
      case "paused":
        return "bg-yellow-500 dark:bg-yellow-400";
      case "error":
        return "bg-red-500 dark:bg-red-400";
      default:
        return "bg-gray-500 dark:bg-gray-400";
    }
  };

  const cancelRun = () => {
    // TODO: ideally this calls stream.client.runs.cancel(threadId, runId)
    stream.stop();
  };

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

  // Merge optimistic message with stream messages
  const displayMessages = optimisticMessage
    ? [
        optimisticMessage,
        ...filteredMessages.filter((msg) => msg.id !== optimisticMessage.id),
      ]
    : filteredMessages;

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
              className={cn(
                "size-2 flex-shrink-0 rounded-full",
                getStatusDotColor(realTimeStatus),
              )}
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
          <ThreadSwitcher currentThread={displayThread} />
          <ThemeToggle />
        </div>
      </div>

      {/* Main Content - Split Layout */}
      <div className="flex w-full pt-12">
        <ManagerChat
          messages={displayMessages}
          chatInput={chatInput}
          setChatInput={setChatInput}
          handleSendMessage={handleSendMessage}
          isLoading={stream.isLoading}
          cancelRun={cancelRun}
          errorState={errorState}
          githubUser={user || undefined}
        />
        {/* Right Side - Actions & Plan */}
        <div
          className="flex flex-1 flex-col px-4 pt-4"
          style={{ height: "calc(100vh - 3rem)" }}
        >
          <div className="min-h-0 flex-1">
            <Tabs
              defaultValue="planner"
              className="flex h-full w-full flex-col"
              value={selectedTab}
              onValueChange={(value) =>
                setSelectedTab(value as "planner" | "programmer")
              }
            >
              <div className="flex flex-shrink-0 items-center gap-3">
                <TabsList className="bg-muted/70">
                  <TabsTrigger value="planner">Planner</TabsTrigger>
                  <TabsTrigger value="programmer">Programmer</TabsTrigger>
                </TabsList>

                {programmerTaskPlan && (
                  <ProgressBar
                    taskPlan={programmerTaskPlan}
                    onOpenSidebar={() => setIsTaskSidebarOpen(true)}
                  />
                )}

                <div className="ml-auto flex items-center justify-center gap-2">
                  {selectedTab === "planner" && plannerStream.isLoading && (
                    <CancelStreamButton
                      stream={plannerStream}
                      threadId={plannerSession?.threadId}
                      runId={plannerSession?.runId}
                      streamName="Planner"
                    />
                  )}

                  {selectedTab === "programmer" &&
                    programmerStream.isLoading && (
                      <CancelStreamButton
                        stream={programmerStream}
                        threadId={programmerSession?.threadId}
                        runId={programmerSession?.runId}
                        streamName="Programmer"
                      />
                    )}
                  <TokenUsage
                    tokenData={joinTokenData(
                      plannerStream.values.tokenData,
                      programmerStream.values.tokenData,
                    )}
                  />
                </div>
              </div>

              <TabsContent
                value="planner"
                className="mb-2"
              >
                <Card className="border-border bg-card relative h-full p-0">
                  <CardContent className="h-full p-0">
                    <StickToBottom
                      className="absolute inset-0 h-full"
                      initial={true}
                    >
                      <StickyToBottomContent
                        className="scrollbar-pretty-auto h-full"
                        content={
                          <>
                            {plannerSession ? (
                              <div className="scrollbar-pretty-auto overflow-y-auto px-2">
                                <ActionsRenderer<PlannerGraphState>
                                  runId={plannerSession.runId}
                                  customNodeEvents={customPlannerNodeEvents}
                                  setCustomNodeEvents={
                                    setCustomPlannerNodeEvents
                                  }
                                  stream={plannerStream}
                                  threadId={plannerSession.threadId}
                                />
                              </div>
                            ) : (
                              <div className="flex items-center justify-center gap-2 py-8">
                                <Clock className="text-muted-foreground size-4" />
                                <span className="text-muted-foreground text-sm">
                                  No planner session
                                </span>
                              </div>
                            )}
                          </>
                        }
                        footer={
                          <div className="absolute right-0 bottom-4 left-0 flex w-full justify-center">
                            <ScrollToBottom className="animate-in fade-in-0 zoom-in-95" />
                          </div>
                        }
                      />
                    </StickToBottom>
                  </CardContent>
                </Card>
              </TabsContent>
              <TabsContent
                value="programmer"
                className="mb-2"
              >
                <Card className="border-border bg-card relative h-full p-0">
                  <CardContent className="h-full p-0">
                    <StickToBottom
                      className="absolute inset-0 h-full"
                      initial={true}
                    >
                      <StickyToBottomContent
                        className="scrollbar-pretty-auto h-full"
                        content={
                          <>
                            {programmerSession ? (
                              <div className="scrollbar-pretty-auto overflow-y-auto px-2">
                                <ActionsRenderer<GraphState>
                                  runId={programmerSession.runId}
                                  customNodeEvents={customProgrammerNodeEvents}
                                  setCustomNodeEvents={
                                    setCustomProgrammerNodeEvents
                                  }
                                  stream={programmerStream}
                                  threadId={programmerSession.threadId}
                                  modifyRunId={async (runId) => {
                                    setProgrammerSession((prev) => {
                                      if (!prev) {
                                        return {
                                          threadId: programmerSession.threadId,
                                          runId,
                                        };
                                      }
                                      return {
                                        ...prev,
                                        runId,
                                      };
                                    });
                                    if (plannerSession?.threadId) {
                                      try {
                                        // Attempt to update the planner session with the new run ID of the programmer.
                                        await programmerStream.client.threads.updateState(
                                          plannerSession?.threadId,
                                          {
                                            values: {
                                              programmerSession: {
                                                threadId:
                                                  programmerSession.threadId,
                                                runId,
                                              },
                                            },
                                          },
                                        );
                                      } catch {
                                        // no-op
                                      }
                                    }
                                  }}
                                />
                              </div>
                            ) : (
                              <div className="flex items-center justify-center gap-2 py-8">
                                <Clock className="text-muted-foreground size-4" />
                                <span className="text-muted-foreground text-sm">
                                  No programmer session
                                </span>
                              </div>
                            )}
                          </>
                        }
                        footer={
                          <div className="absolute right-0 bottom-4 left-0 flex w-full justify-center">
                            <ScrollToBottom className="animate-in fade-in-0 zoom-in-95" />
                          </div>
                        }
                      />
                    </StickToBottom>
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>
          </div>
        </div>
      </div>

      {/* Task Sidebar */}
      {programmerTaskPlan && (
        <TasksSidebar
          isOpen={isTaskSidebarOpen}
          onClose={() => setIsTaskSidebarOpen(false)}
          taskPlan={programmerTaskPlan}
        />
      )}
    </div>
  );
}
