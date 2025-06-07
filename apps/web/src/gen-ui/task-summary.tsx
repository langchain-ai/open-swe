"use client"

import "../app/globals.css"
import { useState } from "react"
import { CheckCircle, Loader2, ChevronDown, ChevronUp, MessageSquare, FileText } from "lucide-react"

type TaskSummaryProps = {
  status: "loading" | "generating" | "done"
  summary?: string
  reasoningText?: string
  summaryText?: string
}

export function TaskSummary({ status, summary, reasoningText, summaryText }: TaskSummaryProps) {
  const [expanded, setExpanded] = useState(false)
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
        return "Preparing task summary..."
      case "generating":
        return "Generating summary..."
      case "done":
        return "Task completed"
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
        className={`flex items-center p-2 bg-gray-50 ${status === "done" && summary ? "cursor-pointer" : ""}`}
        onClick={status === "done" && summary ? () => setExpanded(!expanded) : undefined}
      >
        {getStatusIcon()}
        <span className="text-xs font-normal flex-1 ml-2 text-gray-800">{getStatusText()}</span>
        {status === "done" && summary && (
          <button className="text-gray-500 hover:text-gray-700">
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>

      {expanded && summary && status === "done" && (
        <div className="p-2 border-t border-gray-200">
          <h3 className="text-xs font-normal text-gray-500 mb-1">Task Summary</h3>
          <p className="text-xs font-normal text-gray-800">{summary}</p>
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
