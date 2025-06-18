"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { ArrowLeft, Loader2 } from "lucide-react";

interface ThreadViewLoadingProps {
  onBackToHome?: () => void;
}

const isEven = (num: number) => num % 2 === 0;

export function ThreadViewLoading({ onBackToHome }: ThreadViewLoadingProps) {
  return (
    <div className="flex h-screen flex-1 flex-col bg-black">
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
            <div className="h-2 w-2 animate-pulse rounded-full bg-gray-700"></div>
            <div className="h-3 w-48 animate-pulse rounded bg-gray-700"></div>
            <span className="text-xs text-gray-600">•</span>
            <div className="h-3 w-24 animate-pulse rounded bg-gray-700"></div>
          </div>
          <div className="h-7 w-28 animate-pulse rounded bg-gray-800"></div>
        </div>
      </div>

      <div className="flex h-full w-full pt-12">
        <div className="flex h-full w-1/3 flex-col border-r border-gray-900 bg-gray-950">
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            <div className="flex items-center justify-center py-8">
              <div className="flex items-center gap-2 text-gray-500">
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
                  <div className="h-6 w-6 animate-pulse rounded-full bg-gray-700"></div>
                </div>
                <div className="flex-1 space-y-2">
                  <div className="flex items-center gap-2">
                    <div className="h-3 w-16 animate-pulse rounded bg-gray-700"></div>
                    <div className="h-3 w-12 animate-pulse rounded bg-gray-700"></div>
                  </div>
                  <div className="space-y-1">
                    <div className="h-3 w-full animate-pulse rounded bg-gray-700"></div>
                    <div className="h-3 w-3/4 animate-pulse rounded bg-gray-700"></div>
                    {i === 1 && (
                      <div className="h-3 w-1/2 animate-pulse rounded bg-gray-700"></div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="border-t border-gray-800 bg-gray-950 p-4">
            <div className="flex gap-2">
              <div className="flex-1 rounded-md border border-gray-700 bg-gray-900 p-3">
                <div className="space-y-2">
                  <div className="h-3 w-1/3 animate-pulse rounded bg-gray-700"></div>
                  <div className="h-3 w-1/2 animate-pulse rounded bg-gray-700"></div>
                </div>
              </div>
              <div className="h-10 w-10 animate-pulse self-end rounded bg-gray-700"></div>
            </div>
            <div className="mt-2 h-3 w-32 animate-pulse rounded bg-gray-700"></div>
          </div>
        </div>

        <div className="flex h-full flex-1 flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            <Card className="border-gray-800 bg-gray-950">
              <CardHeader className="p-3">
                <div className="flex items-center gap-2">
                  <div className="h-4 w-32 animate-pulse rounded bg-gray-700"></div>
                  <div className="ml-auto flex items-center gap-1">
                    <Loader2 className="h-3 w-3 animate-spin text-gray-500" />
                    <span className="text-xs text-gray-500">
                      Loading actions...
                    </span>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 p-3 pt-0">
                {Array.from({ length: 8 }, (_, i) => (
                  <div
                    key={i}
                    className="space-y-2 rounded-lg border border-gray-800 bg-gray-900 p-3"
                  >
                    {isEven(i) ? (
                      <div className="space-y-1 rounded bg-black p-2">
                        <div className="h-2 w-32 animate-pulse rounded bg-gray-700"></div>
                        <div className="h-2 w-48 animate-pulse rounded bg-gray-700"></div>
                      </div>
                    ) : (
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <div className="h-4 w-4 animate-pulse rounded bg-gray-700"></div>
                          <div className="h-3 w-40 animate-pulse rounded bg-gray-700"></div>
                        </div>
                        <div className="flex items-center gap-2">
                          <div className="h-3 w-16 animate-pulse rounded bg-gray-700"></div>
                          <div className="h-4 w-4 animate-pulse rounded bg-gray-700"></div>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
                <div className="space-y-2 rounded-lg border-2 border-dashed border-gray-700 p-3">
                  <div className="h-12 animate-pulse rounded bg-gray-800"></div>
                  <div className="h-7 w-16 animate-pulse rounded bg-gray-800"></div>
                </div>

                <div className="flex gap-2 pt-3">
                  <div className="h-8 flex-1 animate-pulse rounded bg-gray-700"></div>
                  <div className="h-8 flex-1 animate-pulse rounded bg-gray-700"></div>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
