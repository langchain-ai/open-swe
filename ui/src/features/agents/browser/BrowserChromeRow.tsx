import { useEffect, useRef, useState } from "react"
import type { FormEvent, KeyboardEvent, ReactNode } from "react"
import { ArrowLeft, ArrowRight, ExternalLink, RotateCw, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

interface Props {
  url: string
  loading: boolean
  canGoBack: boolean
  canGoForward: boolean
  refreshDisabled: boolean
  inputDisabled?: boolean
  /** Focuses the address bar while true, so a new tab can be typed into at once. */
  autoFocus?: boolean
  onBack: () => void
  onForward: () => void
  onRefresh: () => void
  onStop?: () => void
  onSubmit: (url: string) => void
  /** Renders an "open in system browser" affordance on hover. */
  onOpenExternal?: () => void
  /** Trailing slot after the URL input, for the more menu. */
  trailingActions?: ReactNode
}

const NOOP = () => {}

function NavButton(props: {
  label: string
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={props.disabled ? NOOP : props.onClick}
            disabled={props.disabled}
            aria-label={props.label}
            type="button"
          />
        }
      >
        {props.children}
      </TooltipTrigger>
      <TooltipPopup>{props.label}</TooltipPopup>
    </Tooltip>
  )
}

/** Back, forward, reload, and the address bar. */
export function BrowserChromeRow({
  url,
  loading,
  canGoBack,
  canGoForward,
  refreshDisabled,
  inputDisabled,
  autoFocus = false,
  onBack,
  onForward,
  onRefresh,
  onStop,
  onSubmit,
  onOpenExternal,
  trailingActions,
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [draft, setDraft] = useState(url)
  const [inputFocused, setInputFocused] = useState(false)

  useEffect(() => {
    if (!autoFocus) return
    const node = inputRef.current
    if (!node) return
    node.focus()
    node.select()
  }, [autoFocus])

  const submit = (event?: FormEvent | KeyboardEvent) => {
    event?.preventDefault()
    const next = draft.trim()
    if (next.length === 0) return
    onSubmit(next)
    inputRef.current?.blur()
  }

  return (
    <div className="relative">
      <form
        onSubmit={submit}
        className="flex h-9 min-h-9 shrink-0 items-center gap-1 border-b border-border/60 bg-background px-2"
        data-browser-chrome-row
      >
        <div
          className="flex items-center gap-0.5"
          role="group"
          aria-label="Navigation"
        >
          <NavButton label="Back" disabled={!canGoBack} onClick={onBack}>
            <ArrowLeft />
          </NavButton>
          <NavButton
            label="Forward"
            disabled={!canGoForward}
            onClick={onForward}
          >
            <ArrowRight />
          </NavButton>
          {loading && onStop ? (
            <NavButton label="Stop" onClick={onStop}>
              <X />
            </NavButton>
          ) : (
            <NavButton
              label="Reload"
              disabled={refreshDisabled}
              onClick={onRefresh}
            >
              <RotateCw className={cn(loading && "animate-spin")} />
            </NavButton>
          )}
        </div>
        <InputGroup className="group/address h-7 flex-1 border-transparent bg-transparent dark:bg-transparent">
          <InputGroupInput
            ref={inputRef}
            value={inputFocused ? draft : url}
            onChange={(event) => setDraft(event.target.value)}
            onFocus={() => {
              setDraft(url)
              setInputFocused(true)
              queueMicrotask(() => inputRef.current?.select())
            }}
            onBlur={() => setInputFocused(false)}
            onKeyDown={(event) => {
              if (event.key === "Enter") submit(event)
              if (event.key === "Escape") {
                event.preventDefault()
                setDraft(url)
                inputRef.current?.blur()
              }
            }}
            placeholder="Enter a URL"
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            disabled={inputDisabled}
            data-browser-url-input
          />
          {onOpenExternal && url && !inputFocused ? (
            <InputGroupAddon align="inline-end">
              <span className="pointer-events-none flex opacity-0 transition-opacity group-hover/address:pointer-events-auto group-hover/address:opacity-100 focus-within:pointer-events-auto focus-within:opacity-100">
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={onOpenExternal}
                        aria-label="Open in system browser"
                        type="button"
                      />
                    }
                  >
                    <ExternalLink />
                  </TooltipTrigger>
                  <TooltipPopup>Open in system browser</TooltipPopup>
                </Tooltip>
              </span>
            </InputGroupAddon>
          ) : null}
        </InputGroup>
        {trailingActions}
      </form>
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute bottom-0 left-0 z-10 h-0.5 rounded-r-full bg-primary transition-[width,opacity] ease-out",
          loading
            ? "w-4/5 opacity-100 duration-[1500ms]"
            : "w-full opacity-0 duration-300"
        )}
      />
    </div>
  )
}
