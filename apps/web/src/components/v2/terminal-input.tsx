"use client"

import type React from "react"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Send, ChevronDown, GitBranch, Folder } from "lucide-react"

interface Repository {
  owner: string
  name: string
  branches: string[]
}

interface TerminalInputProps {
  onSend?: (message: string, repo: Repository, branch: string) => void
  placeholder?: string
  disabled?: boolean
}

const mockRepositories: Repository[] = [
  {
    owner: "open-swe",
    name: "main",
    branches: ["main", "develop", "feature/auth", "hotfix/bug-123"],
  },
  {
    owner: "vercel",
    name: "next.js",
    branches: ["canary", "main", "beta"],
  },
  {
    owner: "facebook",
    name: "react",
    branches: ["main", "experimental", "18.2-dev"],
  },
  {
    owner: "microsoft",
    name: "vscode",
    branches: ["main", "release/1.85", "insider"],
  },
]

export function TerminalInput({ onSend, placeholder = "Enter your command...", disabled = false }: TerminalInputProps) {
  const [message, setMessage] = useState("")
  const [selectedRepo, setSelectedRepo] = useState<Repository>(mockRepositories[0])
  const [selectedBranch, setSelectedBranch] = useState(mockRepositories[0].branches[0])
  const [repoOpen, setRepoOpen] = useState(false)
  const [branchOpen, setBranchOpen] = useState(false)

  const handleSend = () => {
    if (message.trim() && onSend) {
      onSend(message.trim(), selectedRepo, selectedBranch)
      setMessage("")
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleRepoSelect = (repo: Repository) => {
    setSelectedRepo(repo)
    setSelectedBranch(repo.branches[0])
    setRepoOpen(false)
  }

  const handleBranchSelect = (branch: string) => {
    setSelectedBranch(branch)
    setBranchOpen(false)
  }

  return (
    <div className="bg-black border border-gray-600 rounded-md p-2 font-mono text-xs">
      <div className="flex items-start gap-1 text-gray-300">
        {/* User@Host */}
        <span className="text-gray-400">agent</span>
        <span className="text-gray-500">@</span>
        <span className="text-gray-400">ai</span>
        <span className="text-gray-500">:</span>

        {/* Repository Selector */}
        <Popover open={repoOpen} onOpenChange={setRepoOpen}>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              className="h-auto p-0 font-mono text-xs text-white hover:text-gray-300 hover:bg-transparent"
              disabled={disabled}
            >
              <Folder className="h-2 w-2 mr-1" />
              {selectedRepo.owner}/{selectedRepo.name}
              <ChevronDown className="h-2 w-2 ml-1" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-64 p-2" align="start">
            <div className="space-y-1">
              <div className="text-xs font-medium text-gray-700 px-2 py-1">Select Repository</div>
              <ScrollArea className="h-48">
                {mockRepositories.map((repo) => (
                  <Button
                    key={`${repo.owner}/${repo.name}`}
                    variant="ghost"
                    className="w-full justify-start text-xs h-7"
                    onClick={() => handleRepoSelect(repo)}
                  >
                    <Folder className="h-2 w-2 mr-2" />
                    {repo.owner}/{repo.name}
                    {selectedRepo.owner === repo.owner && selectedRepo.name === repo.name && (
                      <Badge variant="secondary" className="ml-auto text-xs">
                        Current
                      </Badge>
                    )}
                  </Button>
                ))}
              </ScrollArea>
            </div>
          </PopoverContent>
        </Popover>

        {/* Branch Selector */}
        <span className="text-gray-500">(</span>
        <Popover open={branchOpen} onOpenChange={setBranchOpen}>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              className="h-auto p-0 font-mono text-xs text-gray-300 hover:text-white hover:bg-transparent"
              disabled={disabled}
            >
              <GitBranch className="h-2 w-2 mr-1" />
              {selectedBranch}
              <ChevronDown className="h-2 w-2 ml-1" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-48 p-2" align="start">
            <div className="space-y-1">
              <div className="text-xs font-medium text-gray-700 px-2 py-1">Select Branch</div>
              <ScrollArea className="h-32">
                {selectedRepo.branches.map((branch) => (
                  <Button
                    key={branch}
                    variant="ghost"
                    className="w-full justify-start text-xs h-6"
                    onClick={() => handleBranchSelect(branch)}
                  >
                    <GitBranch className="h-2 w-2 mr-2" />
                    {branch}
                    {selectedBranch === branch && (
                      <Badge variant="secondary" className="ml-auto text-xs">
                        Current
                      </Badge>
                    )}
                  </Button>
                ))}
              </ScrollArea>
            </div>
          </PopoverContent>
        </Popover>
        <span className="text-gray-500">)</span>

        {/* Prompt */}
        <span className="text-gray-400">$</span>
      </div>

      {/* Multiline Input */}
      <div className="flex gap-2 mt-1">
        <Textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyPress}
          placeholder={placeholder}
          disabled={disabled}
          className="flex-1 bg-transparent border-none text-white placeholder:text-gray-600 focus-visible:ring-0 focus-visible:ring-offset-0 p-0 font-mono text-xs min-h-[40px] resize-none"
          rows={3}
        />
        <Button
          onClick={handleSend}
          disabled={disabled || !message.trim()}
          size="sm"
          className="h-7 w-7 p-0 bg-gray-700 hover:bg-gray-600 self-end"
        >
          <Send className="h-3 w-3" />
        </Button>
      </div>

      {/* Help text */}
      <div className="text-xs text-gray-600 mt-1">Press Cmd+Enter to send</div>
    </div>
  )
}
