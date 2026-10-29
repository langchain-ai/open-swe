import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { useNavigate } from "@tanstack/react-router"
import { useId, useState } from "react"

import { parsePullRequestReference } from "@/features/reviews/search"

/** Jumps to the review of a pasted GitHub pull request link. */
export function OpenPullRequestInput({
  frontend = false,
}: {
  frontend?: boolean
}) {
  const navigate = useNavigate()
  const hintId = useId()
  const [value, setValue] = useState("")
  const [invalid, setInvalid] = useState(false)

  const parse = (text: string) => {
    const number = /^#?([1-9]\d*)$/.exec(text.trim())?.[1]
    return frontend && number && Number.isSafeInteger(Number(number))
      ? { owner: "langchain-ai", repo: "langchainplus", number: Number(number) }
      : parsePullRequestReference(text)
  }

  const open = (text: string) => {
    const pr = parse(text)
    if (!pr) {
      setInvalid(text.trim().length > 0)
      return
    }
    setValue("")
    setInvalid(false)
    void navigate({
      to: "/agents/reviews/$owner/$repo/$number",
      params: { owner: pr.owner, repo: pr.repo, number: String(pr.number) },
      search: frontend ? { view: "frontend" } : {},
    })
  }

  return (
    <form
      className={frontend ? "w-full" : "w-full sm:w-72"}
      onSubmit={(event) => {
        event.preventDefault()
        open(value)
      }}
    >
      <div className="flex items-start gap-space-2">
        <Input
          size="sm"
          className="min-w-0 flex-1"
          debounceMs={0}
          aria-label={
            frontend
              ? "Open Design QA for a pull request"
              : "Open a pull request review"
          }
          isError={invalid}
          aria-describedby={frontend ? hintId : undefined}
          placeholder={
            frontend
              ? "PR number, owner/repo#123, or GitHub link"
              : "Paste a GitHub PR link"
          }
          value={value}
          onChange={(next) => {
            setValue(next)
            setInvalid(false)
          }}
          onPaste={(event) => {
            const text = event.clipboardData.getData("text")
            if (parse(text)) {
              event.preventDefault()
              open(text)
            }
          }}
        />
        {frontend && (
          <Button size="sm" type="submit">
            Open PR
          </Button>
        )}
      </div>
      {frontend && (
        <p
          id={hintId}
          role={invalid ? "alert" : undefined}
          className={`mt-space-2 text-xs ${invalid ? "text-error-secondary" : "text-secondary"}`}
        >
          {invalid
            ? "Enter a PR number, owner/repo#123, or a GitHub PR link."
            : "Numbers open langchain-ai/langchainplus."}
        </p>
      )}
    </form>
  )
}
