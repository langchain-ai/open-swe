"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  CheckCircle,
  XCircle,
  Loader2,
  GitBranch,
  Layers3,
  Plus,
  Bug,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { ThreadMetadata } from "./types";
import { ThreadCard } from "./thread-card";

interface ThreadSwitcherProps {
  currentThread: ThreadMetadata;
  allThreads: ThreadMetadata[];
}

export function ThreadSwitcher({
  currentThread,
  allThreads,
}: ThreadSwitcherProps) {
  const [open, setOpen] = useState(false);
  const router = useRouter();

  const otherThreads = allThreads.filter((t) => t.id !== currentThread.id);

  return (
    <Sheet
      open={open}
      onOpenChange={setOpen}
    >
      <SheetTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground h-7 gap-1 text-xs"
        >
          <Layers3 className="h-3 w-3" />
          <span className="hidden sm:inline">Switch Thread</span>
        </Button>
      </SheetTrigger>
      <SheetContent
        side="right"
        className="border-border bg-background w-80 sm:w-96"
      >
        <SheetHeader className="pb-4">
          <SheetTitle className="text-foreground text-base">
            All Threads
          </SheetTitle>
        </SheetHeader>

        <div className="mx-2 h-full space-y-3">
          {/* New Chat Button */}
          <Button
            onClick={() => {
              router.push("/chat");
              setOpen(false);
            }}
            className="border-border bg-card text-foreground hover:bg-muted h-8 w-full justify-start gap-2 text-xs"
            variant="outline"
          >
            <Plus className="h-3 w-3" />
            Start New Chat
          </Button>

          {/* Current Thread */}
          <div className="space-y-2">
            <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
              Current Thread
            </h3>
            <ThreadCard thread={currentThread} />
          </div>

          {/* Other Threads */}
          {otherThreads.length > 0 && (
            <div className="h-full space-y-2">
              <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                Other Threads
              </h3>
              <ScrollArea className="h-full">
                <div className="space-y-1">
                  {otherThreads.map((thread) => (
                    <ThreadCard
                      thread={thread}
                      key={thread.id}
                    />
                  ))}
                </div>
              </ScrollArea>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
