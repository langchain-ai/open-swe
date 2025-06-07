"use client"

import "../app/globals.css"
import { useState } from "react"
import { Terminal, Loader2, CheckCircle, XCircle, ChevronDown, ChevronUp, MessageSquare, FileText } from "lucide-react"

type ShellProps = {
  command: string
  status: "loading" | "generating" | "done"
  success?: boolean
  output?: string
  errorCode?: number
  reasoningText?: string
  summaryText?: string
}

export function Shell({ command, status, success, output, errorCode, reasoningText, summaryText }: ShellProps) {
  const [expanded, setExpanded] = useState(status === "done")
  const [showReasoning, setShowReasoning] = useState(false)
  const [showSummary, setShowSummary] = useState(false)

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return <div className="h-3.5 w-3.5 rounded-full border border-gray-300" />
      case "generating":
        return <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500" />
      case "done":
        return success ? (
          <CheckCircle className="h-3.5 w-3.5 text-green-500" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-red-500" />
        )
    }
  }

  const getStatusText = () => {
    switch (status) {
      case "loading":
        return "Preparing command..."
      case "generating":
        return "Executing..."
      case "done":
        return success ? "Completed" : "Failed"
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

      <div className="flex items-center p-2 bg-gray-50 border-b border-gray-200">
        <Terminal className="h-3.5 w-3.5 mr-2 text-gray-500" />
        <code className="text-xs font-normal flex-1 text-gray-800">{command}</code>
        <div className="flex items-center gap-2">
          <span className="text-xs font-normal text-gray-500">{getStatusText()}</span>
          {getStatusIcon()}
          {output && status === "done" && (
            <button onClick={() => setExpanded(!expanded)} className="text-gray-500 hover:text-gray-700">
              {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
      </div>

      {output && expanded && status === "done" && (
        <div className="p-2 bg-gray-900 text-gray-200 overflow-x-auto">
          <pre className="text-xs font-normal whitespace-pre-wrap">{output}</pre>
          {errorCode !== undefined && !success && (
            <div className="mt-1 text-xs text-red-400">Exit code: {errorCode}</div>
          )}
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
