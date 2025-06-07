"use client"

import "../app/globals.css"
import { AlertTriangle, Loader2, CheckCircle, MessageSquare, FileText } from "lucide-react"
import { useState } from "react"

type DiagnoseErrorProps = {
  status: "loading" | "generating" | "done"
  diagnosis?: string
  recommendation?: string
  reasoningText?: string
  summaryText?: string
}

export function DiagnoseError({ status, diagnosis, recommendation, reasoningText, summaryText }: DiagnoseErrorProps) {
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
        return "Preparing error analysis..."
      case "generating":
        return "Diagnosing errors..."
      case "done":
        return "Error diagnosis complete"
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
        <AlertTriangle className="h-3.5 w-3.5 mr-2 text-amber-500" />
        <span className="text-xs font-normal flex-1 text-gray-800">{getStatusText()}</span>
        {getStatusIcon()}
      </div>

      {status === "done" && diagnosis && (
        <div className="p-2">
          <div className="mb-2">
            <h3 className="text-xs font-normal text-gray-500 mb-1">Diagnosis</h3>
            <p className="text-xs font-normal text-gray-800">{diagnosis}</p>
          </div>

          {recommendation && (
            <div>
              <h3 className="text-xs font-normal text-gray-500 mb-1">Recommendation</h3>
              <p className="text-xs font-normal text-gray-800">{recommendation}</p>
            </div>
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
