import { useId, useState, type ReactElement } from "react"

import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/gtm-platform-design-system/ui/popover"
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
  const titleId = useId()
  const descriptionId = useId()
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
      <PopoverContent
        align="end"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className="w-80 max-w-(--available-width)"
      >
        <Stack gap="md">
          <Stack gap="xs">
            <Box
              render={<h2 id={titleId} />}
              className="text-label font-medium text-ink"
            >
              {title}
            </Box>
            {description && (
              <Box
                render={<p id={descriptionId} />}
                className="text-meta text-ink-subtle"
              >
                {description}
              </Box>
            )}
          </Stack>
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
            className="resize-y"
            autoFocus
          />
          <Inline gap="sm" justify="end">
            <Button
              size="compact"
              variant="ghost"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button size="compact" onClick={submit}>
              {submitLabel}
            </Button>
          </Inline>
        </Stack>
      </PopoverContent>
    </Popover>
  )
}
