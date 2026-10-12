import { Button } from "@langchain/macaw-components/Button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useState, type ReactElement } from "react"

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
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent
        align="end"
        aria-label={title}
        className="w-80 max-w-[calc(100vw-2rem)]"
      >
        <Text as="h2" variant="sm" weight="semibold">
          {title}
        </Text>
        {description && (
          <Text variant="xs" color="secondary" className="mt-space-1">
            {description}
          </Text>
        )}
        <Textarea
          size="md"
          value={text}
          onChange={setText}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault()
              submit()
            }
          }}
          placeholder={placeholder}
          rows={3}
          className="mt-space-2"
          autoFocus
        />
        <div className="mt-space-2 flex items-center justify-end gap-space-2">
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          <Button size="xs" onClick={submit}>
            {submitLabel}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
