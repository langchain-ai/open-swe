"use client";

import type React from "react";

import { useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Send } from "lucide-react";
import { RepositoryBranchSelectors } from "../github/repo-branch-selectors";
import { Button } from "../ui/button";

interface TerminalInputProps {
  onSend?: (message: string) => void;
  placeholder?: string;
  disabled?: boolean;
}

export function TerminalInput({
  onSend,
  placeholder = "Enter your command...",
  disabled = false,
}: TerminalInputProps) {
  const [message, setMessage] = useState("");

  const handleSend = () => {
    if (message.trim() && onSend) {
      onSend(message.trim());
      setMessage("");
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="rounded-md border border-gray-600 bg-black p-2 font-mono text-xs">
      <div className="flex items-start gap-1 text-gray-300">
        {/* User@Host */}
        <span className="text-gray-400">open-swe</span>
        <span className="text-gray-500">@</span>
        <span className="text-gray-400">github</span>
        <span className="text-gray-500">:</span>

        {/* Repository & Branch Selectors */}
        <RepositoryBranchSelectors />

        {/* Prompt */}
        <span className="text-gray-400">$</span>
      </div>

      {/* Multiline Input */}
      <div className="mt-1 flex gap-2">
        <Textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyPress}
          placeholder={placeholder}
          disabled={disabled}
          className="min-h-[40px] flex-1 resize-none border-none bg-transparent p-0 font-mono text-xs text-white placeholder:text-gray-600 focus-visible:ring-0 focus-visible:ring-offset-0"
          rows={3}
        />
        <Button
          onClick={handleSend}
          disabled={disabled || !message.trim()}
          size="sm"
          className="h-7 w-7 self-end bg-gray-700 p-0 hover:bg-gray-600"
        >
          <Send className="h-3 w-3" />
        </Button>
      </div>

      {/* Help text */}
      <div className="mt-1 text-xs text-gray-600">Press Cmd+Enter to send</div>
    </div>
  );
}
