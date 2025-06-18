"use client"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { ArrowLeft, Loader2 } from "lucide-react"

interface ThreadViewLoadingProps {
  onBackToHome?: () => void
}

const isEven = (num: number) => num % 2 === 0;

export function ThreadViewLoading({ onBackToHome }: ThreadViewLoadingProps) {
  return (
    <div className="flex-1 flex flex-col bg-black h-screen">
      <div className="absolute top-0 left-0 right-0 z-10 border-b border-gray-900 bg-black px-4 py-2">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-gray-600 hover:text-gray-400 hover:bg-gray-900"
            onClick={onBackToHome}
          >
            <ArrowLeft className="h-3 w-3" />
          </Button>
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <div className="w-2 h-2 rounded-full bg-gray-700 animate-pulse"></div>
            <div className="h-3 bg-gray-700 rounded animate-pulse w-48"></div>
            <span className="text-xs text-gray-600">•</span>
            <div className="h-3 bg-gray-700 rounded animate-pulse w-24"></div>
          </div>
          <div className="h-7 bg-gray-800 rounded animate-pulse w-28"></div>
        </div>
      </div>

      <div className="flex w-full h-full pt-12">
        <div className="w-1/3 border-r border-gray-900 flex flex-col bg-gray-950 h-full">
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <div className="flex items-center justify-center py-8">
              <div className="flex items-center gap-2 text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-sm">Loading conversation...</span>
              </div>
            </div>

            {Array.from({ length: 3 }, (_, i) => (
              <div key={i} className="flex gap-3">
                <div className="flex-shrink-0">
                  <div className="w-6 h-6 bg-gray-700 rounded-full animate-pulse"></div>
                </div>
                <div className="flex-1 space-y-2">
                  <div className="flex items-center gap-2">
                    <div className="h-3 bg-gray-700 rounded animate-pulse w-16"></div>
                    <div className="h-3 bg-gray-700 rounded animate-pulse w-12"></div>
                  </div>
                  <div className="space-y-1">
                    <div className="h-3 bg-gray-700 rounded animate-pulse w-full"></div>
                    <div className="h-3 bg-gray-700 rounded animate-pulse w-3/4"></div>
                    {i === 1 && <div className="h-3 bg-gray-700 rounded animate-pulse w-1/2"></div>}
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="border-t border-gray-800 p-4 bg-gray-950">
            <div className="flex gap-2">
              <div className="flex-1 bg-gray-900 border border-gray-700 rounded-md p-3">
                <div className="space-y-2">
                  <div className="h-3 bg-gray-700 rounded animate-pulse w-1/3"></div>
                  <div className="h-3 bg-gray-700 rounded animate-pulse w-1/2"></div>
                </div>
              </div>
              <div className="h-10 w-10 bg-gray-700 rounded animate-pulse self-end"></div>
            </div>
            <div className="h-3 bg-gray-700 rounded animate-pulse w-32 mt-2"></div>
          </div>
        </div>

        <div className="flex-1 flex flex-col h-full">
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <Card className="bg-gray-950 border-gray-800">
              <CardHeader className="p-3">
                <div className="flex items-center gap-2">
                  <div className="h-4 bg-gray-700 rounded animate-pulse w-32"></div>
                  <div className="flex items-center gap-1 ml-auto">
                    <Loader2 className="h-3 w-3 animate-spin text-gray-500" />
                    <span className="text-xs text-gray-500">Loading actions...</span>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 p-3 pt-0">
                {Array.from({ length: 8 }, (_, i) => (
                  <div key={i} className="border border-gray-800 rounded-lg p-3 space-y-2 bg-gray-900">
                    {isEven(i) ? (
                      <div className="bg-black rounded p-2 space-y-1">
                        <div className="h-2 bg-gray-700 rounded animate-pulse w-32"></div>
                        <div className="h-2 bg-gray-700 rounded animate-pulse w-48"></div>
                      </div>
                    ): (
                      <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <div className="w-4 h-4 bg-gray-700 rounded animate-pulse"></div>
                        <div className="h-3 bg-gray-700 rounded animate-pulse w-40"></div>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="h-3 bg-gray-700 rounded animate-pulse w-16"></div>
                        <div className="w-4 h-4 bg-gray-700 rounded animate-pulse"></div>
                      </div>
                    </div>
                    )}
                  </div>
                ))}
                <div className="border-2 border-dashed border-gray-700 rounded-lg p-3 space-y-2">
                  <div className="h-12 bg-gray-800 rounded animate-pulse"></div>
                  <div className="h-7 bg-gray-800 rounded animate-pulse w-16"></div>
                </div>

                <div className="flex gap-2 pt-3">
                  <div className="flex-1 h-8 bg-gray-700 rounded animate-pulse"></div>
                  <div className="flex-1 h-8 bg-gray-700 rounded animate-pulse"></div>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </div>
  )
}
