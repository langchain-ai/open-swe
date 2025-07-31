"use client";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { ArrowLeft, Loader2 } from "lucide-react";

interface ThreadViewLoadingProps {
  onBackToHome?: () => void;
}

function isEven(n: number) {
  return n % 2 === 0;
}

export function LoadingActionsCardContent() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 8 }, (_, i) => (
        <div
          key={i}
          className="border-border bg-muted/30 space-y-2 rounded-lg border p-3"
        >
          {isEven(i) ? (
            <div className="bg-background space-y-1 rounded p-2">
              <div className="bg-muted h-2 w-32 animate-pulse rounded"></div>
              <div className="bg-muted h-2 w-48 animate-pulse rounded"></div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="bg-muted h-4 w-4 animate-pulse rounded"></div>
                <div className="bg-muted h-3 w-40 animate-pulse rounded"></div>
              </div>
              <div className="flex items-center gap-2">
                <div className="bg-muted h-3 w-16 animate-pulse rounded"></div>
                <div className="bg-muted h-4 w-4 animate-pulse rounded"></div>
              </div>
            </div>
          )}
        </div>
      ))}
      <div className="border-border space-y-2 rounded-lg border-2 border-dashed p-3">
        <div className="bg-muted h-12 animate-pulse rounded"></div>
        <div className="bg-muted h-7 w-16 animate-pulse rounded"></div>
      </div>

      <div className="flex gap-2 pt-3">
        <div className="bg-muted h-8 flex-1 animate-pulse rounded"></div>
        <div className="bg-muted h-8 flex-1 animate-pulse rounded"></div>
      </div>
    </div>
  );
}

export function LoadingActionsCard() {
  return (
    <Card className="border-border bg-card">
      <CardHeader className="p-3">
        <div className="flex items-center gap-2">
          <div className="bg-muted h-4 w-32 animate-pulse rounded"></div>
          <div className="ml-auto flex items-center gap-2">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span className="text-muted-foreground text-xs">
              Loading actions...
            </span>
          </div>
        </div>
      </CardHeader>
      <div className="p-3">
        <LoadingActionsCardContent />
      </div>
    </Card>
  );
}

export function LoadingChatSection() {
  return (
    <div className="flex-1 space-y-4 overflow-y-auto p-4">
      <div className="flex items-center justify-center py-8">
        <div className="text-muted-foreground flex items-center gap-3">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">Loading conversation...</span>
        </div>
      </div>

      {Array.from({ length: 3 }, (_, i) => (
        <div
          key={i}
          className="flex gap-3"
        >
          <div className="flex-shrink-0">
            <div className="bg-muted h-6 w-6 animate-pulse rounded-full"></div>
          </div>
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-2">
              <div className="bg-muted h-3 w-16 animate-pulse rounded"></div>
              <div className="bg-muted h-3 w-12 animate-pulse rounded"></div>
            </div>
            <div className="space-y-1">
              <div className="bg-muted h-3 w-full animate-pulse rounded"></div>
              <div className="bg-muted h-3 w-3/4 animate-pulse rounded"></div>
              {i === 1 && (
                <div className="bg-muted h-3 w-1/2 animate-pulse rounded"></div>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function ThreadViewLoading({ onBackToHome }: ThreadViewLoadingProps) {
  return (
    <div className="bg-background flex h-screen flex-1 flex-col">
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
            <div className="bg-muted h-2 w-2 animate-pulse rounded-full"></div>
            <div className="bg-muted h-3 w-48 animate-pulse rounded"></div>
            <span className="text-muted-foreground text-xs">•</span>
            <div className="bg-muted h-3 w-24 animate-pulse rounded"></div>
          </div>
          <div className="bg-muted/70 h-7 w-28 animate-pulse rounded"></div>
        </div>
      </div>

      <div className="flex h-full w-full pt-12">
        <div className="border-border bg-muted/30 flex h-full w-1/3 flex-col border-r">
          <LoadingChatSection />

          <div className="border-border bg-muted/30 border-t p-4">
            <div className="flex gap-2">
              <div className="border-border bg-background flex-1 rounded-md border p-3">
                <div className="space-y-2">
                  <div className="bg-muted h-3 w-1/3 animate-pulse rounded"></div>
                  <div className="bg-muted h-3 w-1/2 animate-pulse rounded"></div>
                </div>
              </div>
              <div className="bg-muted h-10 w-10 animate-pulse self-end rounded"></div>
            </div>
            <div className="bg-muted mt-2 h-3 w-32 animate-pulse rounded"></div>
          </div>
        </div>

        <div className="flex h-full flex-1 flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            <LoadingActionsCard />
          </div>
        </div>
      </div>
    </div>
  );
}
