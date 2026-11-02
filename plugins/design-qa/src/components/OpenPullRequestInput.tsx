import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { useId, useState } from "react"

import { parsePullRequestInput } from "../lib/pullRequest"
import { useRouter } from "../lib/router"

/** Opens the Design QA page of a pull request someone types or pastes. */
export function OpenPullRequestInput({
  shared,
}: {
  shared: readonly string[]
}) {
  const { navigate } = useRouter()
  const hintId = useId()
  const [value, setValue] = useState("")
  const [invalid, setInvalid] = useState(false)
  const numbers = shared.length === 1

  const open = (text: string) => {
    const pr = parsePullRequestInput(text, shared)
    if (!pr) {
      setInvalid(text.trim().length > 0)
      return
    }
    setValue("")
    setInvalid(false)
    navigate({ page: "pull", pr, run: null })
  }

  return (
    <form
      className="w-full"
      onSubmit={(event) => {
        event.preventDefault()
        open(value)
      }}
    >
      <div className="flex items-start gap-space-2">
        <Input
          size="md"
          className="min-w-0 flex-1"
          debounceMs={0}
          aria-label="Open Design QA for a pull request"
          isError={invalid}
          aria-describedby={invalid ? hintId : undefined}
          placeholder={
            numbers
              ? "PR number, owner/repo#123, or GitHub link"
              : "owner/repo#123 or GitHub link"
          }
          value={value}
          onChange={(next) => {
            setValue(next)
            setInvalid(false)
          }}
          onPaste={(event) => {
            const text = event.clipboardData.getData("text")
            if (parsePullRequestInput(text, shared)) {
              event.preventDefault()
              open(text)
            }
          }}
        />
        <Button size="md" type="submit">
          Open PR
        </Button>
      </div>
      {invalid && (
        <p
          id={hintId}
          role="alert"
          className="mt-space-2 text-xs text-error-secondary"
        >
          {numbers
            ? "Enter a PR number, owner/repo#123, or a GitHub PR link."
            : "Enter owner/repo#123 or a GitHub PR link. A bare number works when you share a single repository."}
        </p>
      )}
    </form>
  )
}
