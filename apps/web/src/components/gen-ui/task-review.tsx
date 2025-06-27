"use client"

import { useState } from "react"
import { CheckCircle, XCircle, Loader2, ChevronDown, ChevronUp, MessageSquare, FileText } from "lucide-react"

type MarkTaskCompletedProps = {
  status: "loading" | "generating" | "done"
  review?: string
  reasoningText?: string
  summaryText?: string
}

export function MarkTaskCompleted({ status, review, reasoningText, summaryText }: MarkTaskCompletedProps) {
  const [expanded, setExpanded] = useState(!!(status === "done" && review))
  const [showReasoning, setShowReasoning] = useState(false)
  const [showSummary, setShowSummary] = useState(false)

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return <div className="h-3.5 w-3.5 rounded-full border border-gray-300" />
      case "generating":
        return <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500" />
      case "done":
        return <CheckCircle className="h-3.5 w-3.5 text-green-500" />
    }
  }

  const getStatusText = () => {
    switch (status) {
      case "loading":
        return "Preparing task review..."
      case "generating":
        return "Reviewing task completion..."
      case "done":
        return "Task marked as completed"
    }
  }

  return (
    <div className="border border-gray-200 rounded-md overflow-hidden">
      {reasoningText && (
        <div className="p-2 bg-blue-50 border-b border-blue-100">
          <button
            onClick={() => setShowReasoning(!showReasoning)}
            className="flex items-center gap-1 text-xs font-normal text-blue-700 hover:text-blue-800"
          >
            <MessageSquare className="h-3 w-3" />
            {showReasoning ? "Hide reasoning" : "Show reasoning"}
          </button>
          {showReasoning && <p className="mt-1 text-xs font-normal text-blue-800">{reasoningText}</p>}
        </div>
      )}

      <div
        className={`flex items-center p-2 bg-green-50 border-b border-green-200 ${
          status === "done" && review ? "cursor-pointer" : ""
        }`}
        onClick={status === "done" && review ? () => setExpanded((prev) => !prev) : undefined}
      >
        <CheckCircle className="h-3.5 w-3.5 mr-2 text-green-600" />
        <span className="text-xs font-normal flex-1 text-green-800">{getStatusText()}</span>
        <div className="flex items-center gap-2">
          {getStatusIcon()}
          {status === "done" && review && (
            <button className="text-green-600 hover:text-green-700">
              {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
      </div>

      {expanded && review && status === "done" && (
        <div className="p-2 bg-green-50 border-t border-green-200">
          <h3 className="text-xs font-normal text-green-600 mb-1">Final Review</h3>
          <p className="text-xs font-normal text-green-800">{review}</p>
        </div>
      )}

      {summaryText && status === "done" && (
        <div className="p-2 bg-green-50 border-t border-green-100">
          <button
            onClick={() => setShowSummary(!showSummary)}
            className="flex items-center gap-1 text-xs font-normal text-green-700 hover:text-green-800"
          >
            <FileText className="h-3 w-3" />
            {showSummary ? "Hide summary" : "Show summary"}
          </button>
          {showSummary && <p className="mt-1 text-xs font-normal text-green-800">{summaryText}</p>}
        </div>
      )}
    </div>
  )
}

type MarkTaskIncompleteProps = {
  status: "loading" | "generating" | "done"
  review?: string
  additionalActions?: string[]
  reasoningText?: string
  summaryText?: string
}

export function MarkTaskIncomplete({
  status,
  review,
  additionalActions,
  reasoningText,
  summaryText,
}: MarkTaskIncompleteProps) {
  const [expanded, setExpanded] = useState(!!(status === "done" && (review || additionalActions)))
  const [showReasoning, setShowReasoning] = useState(false)
  const [showSummary, setShowSummary] = useState(false)

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return <div className="h-3.5 w-3.5 rounded-full border border-gray-300" />
      case "generating":
        return <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500" />
      case "done":
        return <XCircle className="h-3.5 w-3.5 text-red-500" />
    }
  }

  const getStatusText = () => {
    switch (status) {
      case "loading":
        return "Preparing task review..."
      case "generating":
        return "Reviewing task completion..."
      case "done":
        return "Task marked as incomplete"
    }
  }

  return (
    <div className="border border-gray-200 rounded-md overflow-hidden">
      {reasoningText && (
        <div className="p-2 bg-blue-50 border-b border-blue-100">
          <button
            onClick={() => setShowReasoning(!showReasoning)}
            className="flex items-center gap-1 text-xs font-normal text-blue-700 hover:text-blue-800"
          >
            <MessageSquare className="h-3 w-3" />
            {showReasoning ? "Hide reasoning" : "Show reasoning"}
          </button>
          {showReasoning && <p className="mt-1 text-xs font-normal text-blue-800">{reasoningText}</p>}
        </div>
      )}

      <div
        className={`flex items-center p-2 bg-red-50 border-b border-red-200 ${
          status === "done" && (review || additionalActions) ? "cursor-pointer" : ""
        }`}
        onClick={status === "done" && (review || additionalActions) ? () => setExpanded((prev) => !prev) : undefined}
      >
        <XCircle className="h-3.5 w-3.5 mr-2 text-red-600" />
        <span className="text-xs font-normal flex-1 text-red-800">{getStatusText()}</span>
        <div className="flex items-center gap-2">
          {getStatusIcon()}
          {status === "done" && (review || additionalActions) && (
            <button className="text-red-600 hover:text-red-700">
              {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
      </div>

      {expanded && status === "done" && (review || additionalActions) && (
        <div className="p-2 bg-red-50 space-y-3">
          {review && (
            <div>
              <h3 className="text-xs font-normal text-red-600 mb-1">Final Review</h3>
              <p className="text-xs font-normal text-red-800">{review}</p>
            </div>
          )}

          {additionalActions && additionalActions.length > 0 && (
            <div>
              <h3 className="text-xs font-normal text-red-600 mb-1">
                Additional Actions Required ({additionalActions.length})
              </h3>
              <ol className="space-y-1">
                {additionalActions.map((action, index) => (
                  <li key={index} className="flex items-start gap-2">
                    <div className="flex-shrink-0 w-4 h-4 rounded-full bg-red-200 flex items-center justify-center mt-0.5">
                      <span className="text-xs font-normal text-red-700">{index + 1}</span>
                    </div>
                    <span className="text-xs font-normal text-red-800 flex-1">{action}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      {summaryText && status === "done" && (
        <div className="p-2 bg-red-50 border-t border-red-100">
          <button
            onClick={() => setShowSummary(!showSummary)}
            className="flex items-center gap-1 text-xs font-normal text-red-700 hover:text-red-800"
          >
            <FileText className="h-3 w-3" />
            {showSummary ? "Hide summary" : "Show summary"}
          </button>
          {showSummary && <p className="mt-1 text-xs font-normal text-red-800">{summaryText}</p>}
        </div>
      )}
    </div>
  )
}
