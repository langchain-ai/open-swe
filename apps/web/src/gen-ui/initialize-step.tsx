"use client"

import "../app/globals.css"
import { Loader2, CheckCircle, XCircle, GitBranch, MessageSquare, FileText } from "lucide-react"
import { useState } from "react"

type Step = {
  name: string
  status: "waiting" | "generating" | "success" | "error"
  error?: string
}

type InitializeStepProps = {
  status: "loading" | "generating" | "done"
  success?: boolean
  steps?: Step[]
  reasoningText?: string
  summaryText?: string
}

export function InitializeStep({ status, success, steps, reasoningText, summaryText }: InitializeStepProps) {
  const [showReasoning, setShowReasoning] = useState(false)
  const [showSummary, setShowSummary] = useState(false)

  const stepStatusIcon = {
    waiting: <div className="h-3.5 w-3.5 rounded-full border border-gray-300" />,
    generating: <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500" />,
    success: <CheckCircle className="h-3.5 w-3.5 text-green-500" />,
    error: <XCircle className="h-3.5 w-3.5 text-red-500" />,
  }

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
        return "Preparing environment..."
      case "generating":
        return "Initializing environment..."
      case "done":
        return success ? "Environment ready" : "Initialization failed"
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
        <GitBranch className="h-3.5 w-3.5 mr-2 text-gray-500" />
        <span className="text-xs font-normal flex-1 text-gray-800">{getStatusText()}</span>
        {getStatusIcon()}
      </div>

      {steps && (status === "generating" || status === "done") && (
        <div className="p-2">
          <ul className="space-y-2">
            {steps.map((step, index) => (
              <li key={index} className="flex items-center text-xs">
                <span className="mr-2">{stepStatusIcon[step.status]}</span>
                <span className={`font-normal ${step.status === "error" ? "text-red-500" : "text-gray-800"}`}>
                  {step.name}
                </span>
                {step.error && <span className="ml-2 text-red-500 text-xs">({step.error})</span>}
              </li>
            ))}
          </ul>
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
