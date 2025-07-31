"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  Loader2,
  CheckCircle,
  MessageSquare,
  ChevronDown,
} from "lucide-react";
import { BasicMarkdownText } from "../thread/markdown-text";
import { cn } from "@/lib/utils";

type DiagnoseErrorActionProps = {
  status: "loading" | "generating" | "done";
  diagnosis?: string;
  reasoningText?: string;
};

export function DiagnoseErrorAction({
  status,
  diagnosis,
  reasoningText,
}: DiagnoseErrorActionProps) {
  const [showReasoning, setShowReasoning] = useState(true);

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return <div className="border-border size-3.5 rounded-full border" />;
      case "generating":
        return (
          <Loader2 className="text-muted-foreground size-3.5 animate-spin" />
        );
      case "done":
        return (
          <CheckCircle className="size-3.5 text-green-600 dark:text-green-400" />
        );
    }
  };

  const getStatusText = () => {
    switch (status) {
      case "loading":
        return "Preparing error analysis...";
      case "generating":
        return "Diagnosing errors...";
      case "done":
        return "Error diagnosis complete";
    }
  };

  return (
    <div className="border-border overflow-hidden rounded-md border">
      {reasoningText && (
        <div className="border-b border-l-4 border-blue-200 border-l-blue-500 bg-blue-50 p-2 dark:border-blue-800/50 dark:border-l-blue-400 dark:bg-blue-950/20">
          <button
            onClick={() => setShowReasoning(!showReasoning)}
            className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-blue-700 transition-colors duration-200 hover:text-blue-800 dark:text-blue-300 dark:hover:text-blue-200"
          >
            <ChevronDown
              className={cn(
                "h-3 w-3 transition-transform duration-200",
                showReasoning && "rotate-180",
              )}
            />
            <span>{showReasoning ? "Hide reasoning" : "Show reasoning"}</span>
          </button>
          <AnimatePresence>
            {showReasoning && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2, ease: "easeInOut" }}
                className="overflow-hidden"
              >
                <div className="mt-3 rounded-md bg-blue-50/50 px-3 py-2 dark:bg-blue-900/20">
                  <BasicMarkdownText className="text-xs leading-relaxed text-blue-800 dark:text-blue-200">
                    {reasoningText}
                  </BasicMarkdownText>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      <div className="border-border flex items-center border-b bg-gray-50 p-2 dark:bg-gray-800">
        <AlertTriangle className="mr-2 size-3.5 text-amber-500" />
        <span className="text-foreground/80 flex-1 text-xs font-normal">
          {getStatusText()}
        </span>
        {getStatusIcon()}
      </div>

      {status === "done" && diagnosis && (
        <div className="p-2">
          <div className="mb-2">
            <h3 className="text-muted-foreground mb-1 text-xs font-normal">
              Diagnosis
            </h3>
            <p className="text-foreground/80 text-xs font-normal">
              {diagnosis}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
