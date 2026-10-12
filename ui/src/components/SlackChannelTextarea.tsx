import { Textarea } from "@langchain/macaw-components/Textarea"
import { useMemo, useRef, useState, type ComponentProps } from "react"

import {
  SlackChannelRow,
  slackChannelMatches,
} from "@/components/SlackChannelCombobox"
import {
  detectSlackChannelTrigger,
  slackChannelReference,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import { cn } from "@/lib/utils"

const MAX_SUGGESTIONS = 8

type SlackChannelTextareaProps = Omit<
  ComponentProps<"textarea">,
  "value" | "onChange"
> & {
  value: string
  onValueChange: (value: string) => void
  /** Unstyled `<textarea>` for surfaces that frame the field themselves. */
  bare?: boolean
}

/** A textarea that completes `#channel` into Slack's `<#id|name>` reference. */
export function SlackChannelTextarea({
  value,
  onValueChange,
  bare,
  className,
  onKeyDown,
  onBlur,
  ...props
}: SlackChannelTextareaProps) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [cursor, setCursor] = useState<number | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [dismissedAt, setDismissedAt] = useState<number | null>(null)
  const trigger =
    cursor === null ? null : detectSlackChannelTrigger(value, cursor)
  const directory = useSlackChannelDirectory(trigger !== null)
  const matches = useMemo(
    () =>
      trigger
        ? (directory.data?.channels ?? [])
            .filter((channel) => slackChannelMatches(channel, trigger.query))
            .sort(
              (left, right) => Number(right.is_member) - Number(left.is_member)
            )
            .slice(0, MAX_SUGGESTIONS)
        : [],
    [directory.data, trigger]
  )
  const open =
    trigger !== null && matches.length > 0 && dismissedAt !== trigger.rangeStart
  const active = matches[Math.min(activeIndex, matches.length - 1)]

  const syncCursor = () => {
    const element = ref.current
    setCursor(element ? element.selectionStart : null)
    setActiveIndex(0)
  }

  const insert = (id: string, name: string) => {
    if (!trigger) return
    const replacement = `${slackChannelReference(id, name)} `
    const next = `${value.slice(0, trigger.rangeStart)}${replacement}${value.slice(trigger.rangeEnd)}`
    const caret = trigger.rangeStart + replacement.length
    onValueChange(next)
    setCursor(caret)
    requestAnimationFrame(() => {
      ref.current?.setSelectionRange(caret, caret)
    })
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (open && active && !event.nativeEvent.isComposing) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault()
        const step = event.key === "ArrowDown" ? 1 : -1
        setActiveIndex(
          (index) => (index + step + matches.length) % matches.length
        )
        return
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault()
        insert(active.id, active.name)
        return
      }
      if (event.key === "Escape") {
        event.preventDefault()
        setDismissedAt(trigger.rangeStart)
        return
      }
    }
    onKeyDown?.(event)
  }

  const handleChange = (next: string, caret: number | null) => {
    onValueChange(next)
    setCursor(caret)
    setActiveIndex(0)
  }

  const field = {
    ...props,
    ref,
    value,
    role: open ? "combobox" : undefined,
    "aria-expanded": open ? true : undefined,
    "aria-autocomplete": "list" as const,
    onSelect: syncCursor,
    onKeyDown: handleKeyDown,
    onBlur: (event: React.FocusEvent<HTMLTextAreaElement>) => {
      setCursor(null)
      onBlur?.(event)
    },
  }

  return (
    <div className="relative w-full">
      {bare ? (
        <textarea
          className={className}
          {...field}
          onChange={(event) =>
            handleChange(event.target.value, event.target.selectionStart)
          }
        />
      ) : (
        <Textarea
          autoResize
          className={className}
          size="md"
          {...field}
          onChange={(next) =>
            handleChange(next, ref.current?.selectionStart ?? null)
          }
        />
      )}
      {open && (
        <div
          role="listbox"
          aria-label="Slack channels"
          className="absolute top-full left-0 z-popover mt-space-1 w-full max-w-sm overflow-hidden rounded-lg border border-default bg-elevated p-space-1 text-primary shadow-md"
        >
          {matches.map((channel) => (
            <button
              key={channel.id}
              type="button"
              role="option"
              aria-selected={channel.id === active?.id}
              className={cn(
                "relative flex min-h-7 w-full cursor-default items-center gap-space-2 rounded-md px-space-2 py-space-1 text-left text-xs/relaxed select-none",
                channel.id === active?.id && "bg-elevated-hover text-primary"
              )}
              // Keep focus in the textarea so the caret the insertion anchors to survives.
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() =>
                setActiveIndex(matches.findIndex((m) => m.id === channel.id))
              }
              onClick={() => insert(channel.id, channel.name)}
            >
              <SlackChannelRow channel={channel} id={channel.id} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
