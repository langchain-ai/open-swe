import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { AtIcon } from "@phosphor-icons/react/dist/ssr/At"
import {
  useImperativeHandle,
  useRef,
  useState,
  type ComponentProps,
  type Ref,
} from "react"

import { detectComposerTrigger } from "@/features/agents/components/composer/composerTrigger"
import type { MentionablePerson } from "@/lib/api"
import { plainMention, useMentionablePeople } from "@/lib/people"
import { cn } from "@/lib/utils"

const MAX_SUGGESTIONS = 8

type PersonMentionTextareaProps = Omit<
  ComponentProps<typeof Textarea>,
  "value" | "onChange" | "ref"
> & {
  value: string
  onValueChange: (value: string) => void
  /** The conversation the text is about; people involved in it are suggested first. */
  threadId?: string
  textareaRef?: Ref<HTMLTextAreaElement>
}

/** A textarea that completes `@name` to a person's handle, those involved in the conversation first. */
export function PersonMentionTextarea({
  value,
  onValueChange,
  threadId,
  textareaRef,
  onKeyDown,
  onBlur,
  ...props
}: PersonMentionTextareaProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null)
  const [cursor, setCursor] = useState<number | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [dismissedAt, setDismissedAt] = useState<number | null>(null)
  const detected = cursor === null ? null : detectComposerTrigger(value, cursor)
  const trigger = detected?.kind === "path" ? detected : null
  const people = useMentionablePeople(threadId, trigger !== null)
  const query = trigger?.query.toLowerCase() ?? ""
  const matches = trigger
    ? (people.data?.people ?? [])
        .filter((person) =>
          [person.name, person.login].some((name) =>
            name.toLowerCase().includes(query)
          )
        )
        .slice(0, MAX_SUGGESTIONS)
    : []
  const open =
    trigger !== null && matches.length > 0 && dismissedAt !== trigger.rangeStart
  const active = matches[Math.min(activeIndex, matches.length - 1)]

  useImperativeHandle(textareaRef, () => ref.current as HTMLTextAreaElement)

  const insert = (person: MentionablePerson) => {
    if (!trigger) return
    const replacement = `${plainMention(person)} `
    const next = `${value.slice(0, trigger.rangeStart)}${replacement}${value.slice(trigger.rangeEnd)}`
    const caret = trigger.rangeStart + replacement.length
    onValueChange(next)
    setCursor(caret)
    requestAnimationFrame(() => ref.current?.setSelectionRange(caret, caret))
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
      if (
        (event.key === "Enter" && !event.metaKey && !event.ctrlKey) ||
        event.key === "Tab"
      ) {
        event.preventDefault()
        insert(active)
        return
      }
      if (event.key === "Escape") {
        event.preventDefault()
        event.stopPropagation()
        setDismissedAt(trigger.rangeStart)
        return
      }
    }
    onKeyDown?.(event)
  }

  return (
    <div className="relative w-full">
      <Textarea
        {...props}
        ref={ref}
        value={value}
        role={open ? "combobox" : undefined}
        aria-expanded={open ? true : undefined}
        aria-autocomplete="list"
        onSelect={() => {
          setCursor(ref.current?.selectionStart ?? null)
          setActiveIndex(0)
        }}
        onKeyDown={handleKeyDown}
        onBlur={(event) => {
          setCursor(null)
          onBlur?.(event)
        }}
        onChange={(next) => {
          onValueChange(next)
          setCursor(ref.current?.selectionStart ?? null)
          setActiveIndex(0)
        }}
      />
      {open && (
        <div
          role="listbox"
          aria-label="People"
          className="absolute top-full left-0 z-popover mt-space-1 w-full max-w-sm overflow-hidden rounded-lg border border-default bg-elevated p-space-1 text-primary shadow-md"
        >
          {matches.map((person) => (
            <Button
              color="secondary"
              variant="plain"
              size="sm"
              key={person.id}
              type="button"
              role="option"
              aria-selected={person.id === active?.id}
              className={cn(
                "h-auto w-full cursor-default justify-start gap-space-2 px-space-2 py-space-1 text-left text-xs/relaxed whitespace-normal select-none",
                person.id === active?.id && "bg-elevated-hover"
              )}
              // Keep focus in the textarea so the caret the insertion anchors to survives.
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() =>
                setActiveIndex(matches.findIndex((m) => m.id === person.id))
              }
              onClick={() => insert(person)}
            >
              <AtIcon
                aria-hidden
                className="size-3.5 shrink-0 text-brand-primary"
                weight="bold"
              />
              <span className="shrink-0 font-medium">{person.name}</span>
              <span className="min-w-0 flex-1 truncate text-tertiary">
                {[
                  person.login && person.login !== person.name
                    ? `@${person.login}`
                    : null,
                  person.involved ? "in this conversation" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}
