import { v4 as uuidv4 } from "uuid";
import { ReactNode, useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { useStreamContext } from "@/providers/Stream";
import { useState, FormEvent } from "react";
import { Button } from "../ui/button";
import { Checkpoint, Message } from "@langchain/langgraph-sdk";
import { AssistantMessage, AssistantMessageLoading } from "./messages/ai";
import { HumanMessage } from "./messages/human";
import { ensureToolCallsHaveResponses } from "@/lib/ensure-tool-responses";
import { LangGraphLogoSVG } from "../icons/langgraph";
import { TooltipIconButton } from "../ui/tooltip-icon-button";
import {
  ArrowDown,
  LoaderCircle,
  PanelRightOpen,
  PanelRightClose,
  SquarePen,
  XIcon,
  Settings,
  FilePlus2,
} from "lucide-react";
import { ThemeToggle } from "../theme-toggle";
import { useQueryState, parseAsBoolean, parseAsString } from "nuqs";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";
import TaskListSidebar from "../task-list-sidebar";
import { toast } from "sonner";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Label } from "../ui/label";
import { useFileUpload } from "@/hooks/useFileUpload";
import { ContentBlocksPreview } from "./ContentBlocksPreview";
import { GitHubOAuthButton } from "../github/github-oauth-button";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import TaskList from "../task-list";
import { ConfigurationSidebar } from "../configuration-sidebar";
import { DEFAULT_CONFIG_KEY, useConfigStore } from "@/hooks/useConfigStore";
import { RepositoryBranchSelectors } from "../github/repo-branch-selectors";
import { useRouter } from "next/navigation";
import { OpenPRButton } from "../github/open-pr-button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { BaseMessage } from "@langchain/core/messages";
import { TaskPlanView } from "../tasks";
import { useTaskPlan } from "../tasks/useTaskPlan";
import { isProposedPlanInterrupt } from "@/lib/plan-utils";
import { HumanResponse } from "@langchain/langgraph/prebuilt";
import {
  INITIALIZE_NODE_ID,
  mapCustomEventsToSteps,
} from "@open-swe/shared/open-swe/custom-node-events";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";

function StickyToBottomContent(props: {
  content: ReactNode;
  footer?: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  const context = useStickToBottomContext();
  return (
    <div
      ref={context.scrollRef}
      style={{ width: "100%", height: "100%" }}
      className={props.className}
    >
      <div
        ref={context.contentRef}
        className={props.contentClassName}
      >
        {props.content}
      </div>

      {props.footer}
    </div>
  );
}

function ScrollToBottom(props: { className?: string }) {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();

  if (isAtBottom) return null;
  return (
    <Button
      variant="outline"
      className={props.className}
      onClick={() => scrollToBottom()}
    >
      <ArrowDown className="h-4 w-4" />
      <span>Scroll to bottom</span>
    </Button>
  );
}

export function Thread() {
  const { push } = useRouter();
  const { selectedRepository } = useGitHubAppProvider();
  const { getConfig } = useConfigStore();
  const { taskPlan } = useTaskPlan();

  const [threadId, _setThreadId] = useQueryState("threadId");
  const [taskId, setTaskId] = useQueryState("taskId", parseAsString);
  const [chatHistoryOpen, setChatHistoryOpen] = useQueryState(
    "chatHistoryOpen",
    parseAsBoolean.withDefault(false),
  );
  const [configSidebarOpen, setConfigSidebarOpen] = useState(false);

  const isTaskView = !!taskId;
  const isThreadView = !!threadId;

  // Track previous states to detect navigation changes
  const prevTaskId = useRef(taskId);
  const prevThreadId = useRef(threadId);

  useEffect(() => {
    const isNavigatingToTask = !prevTaskId.current && taskId;
    const isNavigatingToThread = !prevThreadId.current && threadId;
    if ((isNavigatingToTask || isNavigatingToThread) && !chatHistoryOpen) {
      setChatHistoryOpen(true);
    }
    prevTaskId.current = taskId;
    prevThreadId.current = threadId;
  }, [taskId, threadId, chatHistoryOpen, setChatHistoryOpen]);

  useEffect(() => {
    if (taskId && typeof window !== "undefined") {
      // TaskId format is "${threadId}-${taskIndex}", so we can extract the threadId directly
      const taskThreadId = taskId.split("-").slice(0, -1).join("-");
      if (taskThreadId && taskThreadId !== threadId) {
        _setThreadId(taskThreadId);
      }
    }
  }, [taskId, threadId, _setThreadId]);

  const [input, setInput] = useState("");
  const {
    contentBlocks,
    setContentBlocks,
    handleFileUpload,
    dropRef,
    removeBlock,
    dragOver,
    handlePaste,
  } = useFileUpload();
  const [firstTokenReceived, setFirstTokenReceived] = useState(false);
  const isLargeScreen = useMediaQuery("(min-width: 1024px)");

  const stream = useStreamContext();
  const messages = stream.messages;
  const isLoading = stream.isLoading;
  const customEvents = stream.customEvents;

  const lastError = useRef<string | undefined>(undefined);

  const setThreadId = (id: string | null) => {
    _setThreadId(id);

    if (id === null) {
      setTaskId(null);
    }
  };

  useEffect(() => {
    if (!stream.error) {
      lastError.current = undefined;
      return;
    }
    try {
      const message = (stream.error as any).message;
      if (!message || lastError.current === message) {
        return;
      }

      lastError.current = message;
      toast.error("An error occurred. Please try again.", {
        description: (
          <p>
            <strong>Error:</strong> <code>{message}</code>
          </p>
        ),
        richColors: true,
        closeButton: true,
      });
    } catch {
      console.error("Error in stream", stream.error);
    }
  }, [stream.error]);

  const prevMessageLength = useRef(0);
  useEffect(() => {
    if (
      messages.length !== prevMessageLength.current &&
      messages?.length &&
      messages[messages.length - 1].type === "ai"
    ) {
      setFirstTokenReceived(true);
    }

    prevMessageLength.current = messages.length;
  }, [messages]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if ((input.trim().length === 0 && contentBlocks.length === 0) || isLoading)
      return;

    if (!selectedRepository) {
      toast.error("Please select a repository first", {
        description:
          "You need to select a repository before sending a message.",
        richColors: true,
        closeButton: true,
      });
      return;
    }

    setFirstTokenReceived(false);

    if (isProposedPlanInterrupt(stream.interrupt)) {
      const resume: HumanResponse[] = [
        {
          type: "response",
          args: input.trim(),
        },
      ];
      stream.submit(
        {},
        {
          command: {
            resume,
          },
        },
      );
      if (contentBlocks.length > 0) {
        toast.warning(
          "Content blocks were not submitted with the plan response.",
          {
            richColors: true,
            closeButton: true,
          },
        );
      }
      setInput("");
      setContentBlocks([]);
      return;
    }

    const newHumanMessage: Message = {
      id: uuidv4(),
      type: "human",
      content: [
        ...(input.trim().length > 0 ? [{ type: "text", text: input }] : []),
        ...contentBlocks,
      ] as Message["content"],
    };

    const toolMessages = ensureToolCallsHaveResponses(stream.messages);

    const newMessages = [
      ...toolMessages,
      newHumanMessage,
    ] as unknown as BaseMessage[];
    stream.submit(
      {
        messages: newMessages,
        internalMessages: newMessages,
        targetRepository: selectedRepository,
      },
      {
        streamMode: ["values"],
        optimisticValues: (prev) => ({
          ...prev,
          messages: [
            ...(prev.messages ?? []),
            ...toolMessages,
            newHumanMessage,
          ] as unknown as BaseMessage[],
        }),
        config: {
          recursion_limit: 400,
          configurable: {
            ...getConfig(threadId || DEFAULT_CONFIG_KEY),
          },
        },
        metadata: {
          graph_id: process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "open-swe",
        },
      },
    );

    setInput("");
    setContentBlocks([]);
  };

  const handleRegenerate = (
    parentCheckpoint: Checkpoint | null | undefined,
  ) => {
    // Do this so the loading state is correct
    prevMessageLength.current = prevMessageLength.current - 1;
    setFirstTokenReceived(false);
    stream.submit(undefined, {
      checkpoint: parentCheckpoint,
      streamMode: ["values"],
      config: {
        recursion_limit: 400,
        configurable: {
          ...getConfig(threadId || DEFAULT_CONFIG_KEY),
        },
      },
      metadata: {
        graph_id: process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "open-swe",
      },
    });
  };

  const chatStarted = !!threadId || !!messages.length;
  const hasNoAIOrToolMessages = !messages.find(
    (m) => m.type === "ai" || m.type === "tool",
  );
  const isLastMessageHuman = messages[messages.length - 1]?.type === "human";

  const initializeEvents = customEvents.filter(
    (e) => e.nodeId === INITIALIZE_NODE_ID,
  );

  const steps = mapCustomEventsToSteps(initializeEvents);
  const allSuccess =
    steps.length > 0 && steps.every((s) => s.status === "success");

  let initStatus: "loading" | "generating" | "done" = "generating";
  if (allSuccess) {
    initStatus = "done";
  }

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <div className="relative hidden lg:flex">
        <motion.div
          className="absolute z-20 h-full overflow-hidden border-r bg-white"
          style={{ width: 300 }}
          animate={
            isLargeScreen
              ? { x: chatHistoryOpen ? 0 : -300 }
              : { x: chatHistoryOpen ? 0 : -300 }
          }
          initial={{ x: -300 }}
          transition={
            isLargeScreen
              ? { type: "spring", stiffness: 300, damping: 30 }
              : { duration: 0 }
          }
        >
          <div
            className="relative h-full"
            style={{ width: 300 }}
          >
            <TaskListSidebar onCollapse={() => setChatHistoryOpen(false)} />
          </div>
        </motion.div>
      </div>

      <div className="grid w-full grid-cols-[1fr_0fr] transition-all duration-500">
        <motion.div
          className={cn(
            "relative flex min-w-0 flex-1 flex-col overflow-hidden",
            !chatStarted && "grid-rows-[1fr]",
          )}
          layout={isLargeScreen}
          animate={{
            marginLeft: chatHistoryOpen ? (isLargeScreen ? 300 : 0) : 0,
            width: chatHistoryOpen
              ? isLargeScreen
                ? "calc(100% - 300px)"
                : "100%"
              : "100%",
          }}
          transition={
            isLargeScreen
              ? { type: "spring", stiffness: 300, damping: 30 }
              : { duration: 0 }
          }
        >
          {!chatStarted && (
            <div className="absolute top-0 left-0 z-10 flex w-full items-center justify-between gap-3 p-2 pl-4">
              <div>
                {(!chatHistoryOpen || !isLargeScreen) && (
                  <Button
                    className="hover:bg-gray-100"
                    variant="ghost"
                    onClick={() => setChatHistoryOpen((p) => !p)}
                  >
                    {chatHistoryOpen ? (
                      <PanelRightOpen className="size-5" />
                    ) : (
                      <PanelRightClose className="size-5" />
                    )}
                  </Button>
                )}
              </div>
              <div className="absolute top-2 right-4 flex items-center gap-2 text-gray-700">
                <TooltipIconButton
                  tooltip="Configuration"
                  variant="ghost"
                  onClick={() => {
                    setConfigSidebarOpen(true);
                  }}
                >
                  <Settings className="size-4" />
                </TooltipIconButton>
                <ThemeToggle />
                <GitHubOAuthButton />
              </div>
            </div>
          )}
          {chatStarted && (
            <div className="relative z-10 grid grid-cols-10 items-start gap-3 p-2">
              <div className="relative col-span-2 flex items-center justify-start gap-2">
                <div className="absolute left-0 z-10">
                  {(!chatHistoryOpen || !isLargeScreen) && (
                    <Button
                      className="hover:bg-gray-100"
                      variant="ghost"
                      onClick={() => setChatHistoryOpen((p) => !p)}
                    >
                      {chatHistoryOpen ? (
                        <PanelRightOpen className="size-5" />
                      ) : (
                        <PanelRightClose className="size-5" />
                      )}
                    </Button>
                  )}
                </div>
                <motion.button
                  className="flex cursor-pointer items-center gap-2"
                  onClick={() => push("/")}
                  animate={{
                    marginLeft: !chatHistoryOpen ? 48 : 0,
                  }}
                  transition={{
                    type: "spring",
                    stiffness: 300,
                    damping: 30,
                  }}
                >
                  <LangGraphLogoSVG
                    width={32}
                    height={32}
                  />
                  <span className="text-xl font-semibold tracking-tight">
                    Open SWE
                  </span>
                </motion.button>
              </div>

              <div className="col-span-6 mx-auto flex w-sm justify-center md:w-md lg:w-lg xl:w-xl">
                {taskPlan && (
                  <TaskPlanView
                    taskPlan={taskPlan}
                    onTaskChange={() => {}}
                  />
                )}
              </div>

              <div className="col-span-2 flex items-center justify-end gap-2 text-gray-700">
                <GitHubOAuthButton />
                <OpenPRButton />
                <TooltipIconButton
                  tooltip="Configuration"
                  variant="ghost"
                  onClick={() => {
                    setConfigSidebarOpen(true);
                  }}
                >
                  <Settings className="size-4" />
                </TooltipIconButton>
              </div>

              <div className="from-background to-background/0 absolute inset-x-0 top-full h-5 bg-gradient-to-b" />
            </div>
          )}

          <StickToBottom
            className="relative flex-1 overflow-hidden"
            initial={false}
          >
            <StickyToBottomContent
              className={cn(
                "absolute inset-0 overflow-y-scroll px-4 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent",
                !chatStarted && "mt-[10vh] flex flex-col items-stretch",
                chatStarted && "grid grid-rows-[1fr_auto]",
              )}
              contentClassName="pt-8 pb-16  max-w-3xl mx-auto flex flex-col gap-4 w-full"
              content={
                <>
                  {messages
                    .filter((m) => !m.id?.startsWith(DO_NOT_RENDER_ID_PREFIX))
                    .map((message, index) =>
                      message.type === "human" ? (
                        <HumanMessage
                          key={message.id || `${message.type}-${index}`}
                          message={message}
                          isLoading={isLoading}
                        />
                      ) : (
                        <AssistantMessage
                          key={message.id || `${message.type}-${index}`}
                          message={message}
                          isLoading={isLoading}
                          handleRegenerate={handleRegenerate}
                          thread={stream}
                        />
                      ),
                    )}
                  {/* Special rendering case where there are no AI/tool messages, but there is an interrupt.
                    We need to render it outside of the messages list, since there are no messages to render */}
                  {(hasNoAIOrToolMessages || isLastMessageHuman) &&
                    !!stream.interrupt && (
                      <AssistantMessage
                        key="interrupt-msg"
                        message={undefined}
                        isLoading={isLoading}
                        handleRegenerate={handleRegenerate}
                        forceRenderInterrupt={true}
                        thread={stream}
                      />
                    )}
                  {isLoading &&
                    !firstTokenReceived &&
                    initializeEvents.length === 0 && (
                      <AssistantMessageLoading />
                    )}
                </>
              }
              footer={
                <div
                  className={cn(
                    "flex flex-col items-center gap-8 bg-white",
                    !isTaskView && !isThreadView
                      ? "mb-32 pb-32"
                      : "sticky bottom-0",
                  )}
                >
                  {!chatStarted && (
                    <div className="flex items-center gap-3">
                      <LangGraphLogoSVG className="h-8 flex-shrink-0" />
                      <h1 className="text-2xl font-semibold tracking-tight">
                        Open SWE
                      </h1>
                    </div>
                  )}

                  <ScrollToBottom className="animate-in fade-in-0 zoom-in-95 absolute bottom-full left-1/2 mb-4 -translate-x-1/2" />

                  <div
                    ref={dropRef}
                    className={cn(
                      "bg-muted relative z-10 mx-auto mb-8 w-full max-w-3xl rounded-2xl shadow-xs transition-all",
                      dragOver
                        ? "border-primary border-2 border-dotted"
                        : "border border-solid",
                    )}
                  >
                    <form
                      onSubmit={handleSubmit}
                      className="mx-auto grid max-w-3xl grid-rows-[1fr_auto] gap-2"
                    >
                      <ContentBlocksPreview
                        blocks={contentBlocks}
                        onRemove={removeBlock}
                      />
                      <textarea
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onPaste={handlePaste}
                        onKeyDown={(e) => {
                          if (
                            e.key === "Enter" &&
                            !e.shiftKey &&
                            !e.metaKey &&
                            !e.nativeEvent.isComposing
                          ) {
                            e.preventDefault();
                            const el = e.target as HTMLElement | undefined;
                            const form = el?.closest("form");
                            form?.requestSubmit();
                          }
                        }}
                        placeholder="Type your message..."
                        className="field-sizing-content resize-none border-none bg-transparent p-3.5 pb-0 shadow-none ring-0 outline-none focus:ring-0 focus:outline-none"
                      />

                      <div className="flex items-center gap-1 p-2 pt-4">
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger>
                              <Label
                                htmlFor="file-input"
                                className="mx-1 flex h-8 w-8 cursor-pointer items-center justify-center rounded-full border-[1px] border-gray-300 bg-inherit text-gray-500 hover:text-gray-700"
                              >
                                <FilePlus2 className="size-4" />
                              </Label>
                            </TooltipTrigger>
                            <TooltipContent>Attach files</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>

                        <input
                          id="file-input"
                          type="file"
                          onChange={handleFileUpload}
                          multiple
                          accept="image/jpeg,image/png,image/gif,image/webp,application/pdf"
                          className="hidden"
                        />
                        <RepositoryBranchSelectors />
                        {chatStarted && (
                          <TooltipIconButton
                            tooltip="New thread"
                            variant="outline"
                            onClick={() => setThreadId(null)}
                            className="ml-1 h-8 w-8 rounded-full border-gray-300 bg-inherit text-gray-500 hover:text-gray-700"
                          >
                            <SquarePen className="size-4" />
                          </TooltipIconButton>
                        )}

                        {stream.isLoading ? (
                          <Button
                            key="stop"
                            onClick={() => stream.stop()}
                            className="ml-auto"
                          >
                            <LoaderCircle className="size-4 animate-spin" />
                            Cancel
                          </Button>
                        ) : (
                          <Button
                            type="submit"
                            className="ml-auto shadow-md transition-all"
                            disabled={
                              isLoading ||
                              (!input.trim() && contentBlocks.length === 0)
                            }
                          >
                            Send
                          </Button>
                        )}
                      </div>
                    </form>
                  </div>

                  {!isTaskView && !isThreadView && <TaskList />}
                </div>
              }
            />
          </StickToBottom>
        </motion.div>
      </div>

      <ConfigurationSidebar
        open={configSidebarOpen}
        onClose={() => setConfigSidebarOpen(false)}
      />
    </div>
  );
}
