import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useId, useRef, useState, type ComponentProps } from "react"

import { SlackChannelRow } from "@/components/SlackChannelCombobox"
import {
  detectSlackChannelTrigger,
  rankSlackChannels,
  rememberSlackChannels,
  slackChannelReference,
  useRecentSlackChannels,
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
  /** Plain Macaw textarea for surfaces that frame the field themselves. */
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
  const optionIdPrefix = useId()
  const [cursor, setCursor] = useState<number | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [dismissedAt, setDismissedAt] = useState<number | null>(null)
  const trigger =
    cursor === null ? null : detectSlackChannelTrigger(value, cursor)
  const directory = useSlackChannelDirectory(trigger !== null)
  const recent = useRecentSlackChannels()
  const matches = trigger
    ? rankSlackChannels(
        directory.data?.channels ?? [],
        trigger.query,
        recent
      ).slice(0, MAX_SUGGESTIONS)
    : []
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
    rememberSlackChannels([id])
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
      <Textarea
        autoResize={!bare}
        variant={bare ? "plain" : "default"}
        className={
          bare
            ? undefined
            : cn(
                "[&>div]:border-strong/70 [&>div:hover:not(:focus-within)]:border-strong",
                className
              )
        }
        inputClassName={bare ? className : undefined}
        size="md"
        {...field}
        onChange={(next) =>
          handleChange(next, ref.current?.selectionStart ?? null)
        }
      />
      {open && (
        <div
          role="listbox"
          aria-label="Slack channels"
          className="absolute top-full left-0 z-popover mt-space-1 w-full max-w-sm overflow-hidden rounded-lg border border-default bg-elevated p-space-1 text-primary shadow-md"
        >
          {matches.map((channel) => (
            <Button
              color="secondary"
              variant="plain"
              size="sm"
              aria-labelledby={`${optionIdPrefix}-${channel.id}`}
              key={channel.id}
              type="button"
              role="option"
              aria-selected={channel.id === active?.id}
              className={cn(
                "h-auto justify-start whitespace-normal",
                "relative min-h-7 w-full cursor-default gap-space-2 px-space-2 py-space-1 text-left text-xs/relaxed select-none",
                channel.id === active?.id && "bg-elevated-hover text-primary"
              )}
              // Keep focus in the textarea so the caret the insertion anchors to survives.
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() =>
                setActiveIndex(matches.findIndex((m) => m.id === channel.id))
              }
              onClick={() => insert(channel.id, channel.name)}
            >
              <span id={`${optionIdPrefix}-${channel.id}`} className="contents">
                <SlackChannelRow
                  channel={channel}
                  id={channel.id}
                  recent={recent.includes(channel.id)}
                />
              </span>
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}
