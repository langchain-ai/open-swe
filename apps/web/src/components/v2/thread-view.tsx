"use client";

import { v4 as uuidv4 } from "uuid";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  ArrowLeft,
  GitBranch,
  Send,
  User,
  Bot,
  Copy,
  CopyCheck,
} from "lucide-react";
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button";
import { getMessageContentString } from "@open-swe/shared/messages";
import { AnimatePresence, motion } from "framer-motion";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ThreadSwitcher } from "./thread-switcher";
import { ThreadDisplayInfo } from "./types";
import { useStream } from "@langchain/langgraph-sdk/react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { ActionsRenderer } from "./actions-renderer";
import { ThemeToggle } from "../theme-toggle";
import { HumanMessage } from "@langchain/core/messages";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";

const PROGRAMMER_ASSISTANT_ID = process.env.NEXT_PUBLIC_PROGRAMMER_ASSISTANT_ID;
const PLANNER_ASSISTANT_ID = process.env.NEXT_PUBLIC_PLANNER_ASSISTANT_ID;

function MessageCopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent<HTMLButtonElement, MouseEvent>) => {
    e.stopPropagation();
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <TooltipIconButton
      onClick={(e) => handleCopy(e)}
      variant="ghost"
      tooltip="Copy content"
      className="size-6 p-1"
    >
      <AnimatePresence
        mode="wait"
        initial={false}
      >
        {copied ? (
          <motion.div
            key="check"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{ duration: 0.15 }}
          >
            <CopyCheck className="h-3 w-3 text-green-500" />
          </motion.div>
        ) : (
          <motion.div
            key="copy"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{ duration: 0.15 }}
          >
            <Copy className="h-3 w-3" />
          </motion.div>
        )}
      </AnimatePresence>
    </TooltipIconButton>
  );
}

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
  const [hoveredMessageId, setHoveredMessageId] = useState<string | null>(null);
  const plannerThreadId = stream.values?.plannerSession?.threadId;
  const plannerRunId = stream.values?.plannerSession?.runId;
  const [programmerSession, setProgrammerSession] =
    useState<ManagerGraphState["programmerSession"]>();

  if (!stream.messages?.length) {
    return null;
  }

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
        {/* Left Side - Chat Interface */}
        <div className="border-border bg-muted/30 flex h-full w-1/3 flex-col border-r dark:bg-gray-950">
          {/* Chat Messages */}
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {filteredMessages.map((message) => (
              <div
                key={message.id}
                className="group flex gap-3"
                onMouseEnter={() => setHoveredMessageId(message.id)}
                onMouseLeave={() => setHoveredMessageId(null)}
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
                <div className="relative flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-muted-foreground text-xs font-medium">
                      {message.type === "human" ? "You" : "Agent"}
                    </span>
                  </div>
                  <div className="text-foreground text-sm leading-relaxed">
                    {getMessageContentString(message.content)}
                  </div>
                  <div className="absolute top-0 right-0 opacity-0 group-hover:opacity-100 transition-opacity">
                    <MessageCopyButton
                      content={getMessageContentString(message.content)}
                    />
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
              value={selectedTab}
              onValueChange={(value) =>
                setSelectedTab(value as "planner" | "programmer")
              }
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
                          setSelectedTab={setSelectedTab}
                        />
                      )}
                  </CardContent>
                </Card>
              </TabsContent>
              <TabsContent value="programmer">
                <Card className="border-border bg-card px-0 py-4 dark:bg-gray-950">
                  <CardContent className="space-y-2 p-3 pt-0">
                    {programmerSession && PROGRAMMER_ASSISTANT_ID && (
                      <ActionsRenderer<PlannerGraphState>
                        graphId={PROGRAMMER_ASSISTANT_ID}
                        threadId={programmerSession.threadId}
                        runId={programmerSession.runId}
                      />
                    )}
                    {!programmerSession && (
                      <div className="text-muted-foreground text-xs">
                        No programmer session
                      </div>
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


