import { Input } from "@langchain/macaw-components/Input"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { cn } from "@/lib/utils"
import { parsePullRequestReference } from "@/features/reviews/search"

/** Jumps to the review of a pasted GitHub pull request link. */
export function OpenPullRequestInput({ className }: { className?: string }) {
  const navigate = useNavigate({ from: "/agents/reviews/" })
  const [value, setValue] = useState("")
  const [invalid, setInvalid] = useState(false)

  const open = (text: string) => {
    const pr = parsePullRequestReference(text)
    if (!pr) {
      setInvalid(text.trim().length > 0)
      return
    }
    setValue("")
    setInvalid(false)
    void navigate({
      to: "/agents/reviews",
      search: (previous) => ({
        ...previous,
        pr: `${pr.owner}/${pr.repo}#${pr.number}`,
        queue: undefined,
        files: undefined,
        at: undefined,
      }),
    })
  }

  return (
    <form
      className={cn("w-72", className)}
      onSubmit={(event) => {
        event.preventDefault()
        open(value)
      }}
    >
      <Input
        size="sm"
        aria-label="Open a pull request review"
        isError={invalid}
        placeholder="Paste a GitHub PR link"
        value={value}
        onChange={(next) => {
          setValue(next)
          setInvalid(false)
        }}
        onPaste={(event) => {
          const text = event.clipboardData.getData("text")
          if (parsePullRequestReference(text)) {
            event.preventDefault()
            open(text)
          }
        }}
      />
    </form>
  )
}
