import { useEffect, useState } from "react"
import {
  ArrowSquareOutIcon,
  MagnifyingGlassIcon,
  SlackLogoIcon,
} from "@phosphor-icons/react"

import type { SlackMessagePreviewRequest } from "@/features/agents/lib/api"
import { AgentsApiError } from "@/features/agents/lib/api"
import { useSlackMessagePreview } from "@/features/agents/lib/queries"

const PREVIEW_DEBOUNCE_MS = 600
const SLACK_CHANNEL_ID_RE = /^[CG][A-Z0-9]{8,}$/

interface SlackMessageTriggerFieldsProps {
  channelId: string
  onChannelIdChange: (value: string) => void
  pattern: string
  onPatternChange: (value: string) => void
  disabled: boolean
}

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

function previewErrorMessage(error: unknown): string {
  if (error instanceof AgentsApiError && error.status === 422) {
    return "Not a valid RE2 regular expression."
  }
  return error instanceof Error ? error.message : "Preview failed."
}

export function SlackMessageTriggerFields({
  channelId,
  onChannelIdChange,
  pattern,
  onPatternChange,
  disabled,
}: SlackMessageTriggerFieldsProps) {
  const channel = channelId.trim().toUpperCase()
  const request = useDebounced<SlackMessagePreviewRequest | null>(
    SLACK_CHANNEL_ID_RE.test(channel) && pattern
      ? { slack_channel_id: channel, message_pattern: pattern }
      : null,
    PREVIEW_DEBOUNCE_MS
  )
  const preview = useSlackMessagePreview(request)

  return (
    <div className="flex flex-col gap-3 px-3 py-2.5">
      <label className="flex items-center gap-3">
        <SlackLogoIcon className="size-4 shrink-0 text-muted-foreground" />
        <input
          value={channelId}
          onChange={(e) => onChannelIdChange(e.target.value)}
          disabled={disabled}
          placeholder="Channel ID, e.g. C0123456789"
          spellCheck={false}
          aria-label="Watched Slack channel ID"
          className="flex-1 bg-transparent font-mono text-sm text-foreground outline-none placeholder:text-muted-foreground/70"
        />
      </label>
      <label className="flex items-center gap-3">
        <MagnifyingGlassIcon className="size-4 shrink-0 text-muted-foreground" />
        <input
          value={pattern}
          onChange={(e) => onPatternChange(e.target.value)}
          disabled={disabled}
          placeholder="(?i)deploy.*failed"
          spellCheck={false}
          aria-label="Message pattern"
          className="flex-1 bg-transparent font-mono text-sm text-foreground outline-none placeholder:text-muted-foreground/70"
        />
      </label>
      <p className="text-xs text-muted-foreground/70">
        Runs when a new top-level post in this channel matches the pattern (RE2
        syntax, searched anywhere in the text). The agent replies in that post's
        thread. Thread replies, posts that mention Open SWE, and Open SWE's own
        posts never trigger it. The Open SWE bot must be a member of the
        channel.
      </p>

      {request && (
        <div className="border-t border-border/60 pt-3">
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Recent posts that would have matched
          </p>
          {preview.isFetching && !preview.data ? (
            <p className="text-xs text-muted-foreground/70">
              Scanning channel history…
            </p>
          ) : preview.error ? (
            <p className="text-xs text-destructive">
              {previewErrorMessage(preview.error)}
            </p>
          ) : preview.data ? (
            <>
              <p className="mb-2 text-xs text-muted-foreground/70">
                {preview.data.matches.length} of {preview.data.scanned} posts
                from the last {preview.data.days} days matched.
              </p>
              {preview.data.matches.length > 0 && (
                <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto">
                  {preview.data.matches.map((match) => (
                    <li key={match.ts}>
                      <a
                        href={match.permalink}
                        target="_blank"
                        rel="noreferrer"
                        className="group flex items-start gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-accent"
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block text-muted-foreground/70">
                            {new Date(Number(match.ts) * 1000).toLocaleString()}
                          </span>
                          <span className="line-clamp-2 break-words text-foreground">
                            {match.text || "(no text)"}
                          </span>
                        </span>
                        <ArrowSquareOutIcon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/70 group-hover:text-foreground" />
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : null}
        </div>
      )}
    </div>
  )
}
