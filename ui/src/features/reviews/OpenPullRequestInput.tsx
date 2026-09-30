import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { Input } from "@/components/ui/input"
import { parsePullRequestReference } from "@/features/reviews/search"

/** Jumps to the review of a pasted GitHub pull request link. */
export function OpenPullRequestInput() {
  const navigate = useNavigate()
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
      to: "/agents/reviews/$owner/$repo/$number",
      params: { owner: pr.owner, repo: pr.repo, number: String(pr.number) },
    })
  }

  return (
    <form
      className="w-72"
      onSubmit={(event) => {
        event.preventDefault()
        open(value)
      }}
    >
      <Input
        aria-label="Open a pull request review"
        aria-invalid={invalid || undefined}
        placeholder="Paste a GitHub PR link"
        value={value}
        onChange={(event) => {
          setValue(event.target.value)
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
