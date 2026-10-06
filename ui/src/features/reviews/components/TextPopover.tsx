import { useState, type ReactElement } from "react"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Popover, PopoverDescription, PopoverContent, PopoverTitle, PopoverTrigger } from "@langchain/gtm-platform-design-system/ui/popover"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"

/**
 * A trigger that opens an anchored popover asking for optional text before acting.
 * It closes on submit; a rejected `onSubmit` puts the text back for the next open.
 */
export function TextPopover({
  trigger,
  title,
  description,
  placeholder,
  submitLabel,
  onSubmit,
}: {
  trigger: ReactElement
  title: string
  description?: string
  placeholder: string
  submitLabel: string
  onSubmit: (text: string) => Promise<unknown>
}) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState("")
  const submit = () => {
    const submitted = text.trim()
    setOpen(false)
    setText("")
    onSubmit(submitted).catch(() => {
      setText((current) => current || submitted)
    })
  }
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={trigger} />
      <PopoverContent align="end" className="w-80 max-w-[calc(100vw-2rem)]">
        <PopoverTitle className="text-label">{title}</PopoverTitle>
        {description && (
          <PopoverDescription className="mt-1">
            {description}
          </PopoverDescription>
        )}
        <Textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault()
              submit()
            }
          }}
          placeholder={placeholder}
          rows={3}
          className="mt-2 resize-y text-label"
          autoFocus
        />
        <div className="mt-2 flex items-center justify-end gap-2">
          <Button size="compact" variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button size="compact" onClick={submit}>
            {submitLabel}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
