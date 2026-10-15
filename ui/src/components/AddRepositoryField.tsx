import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Typeahead } from "@langchain/macaw-components/Typeahead"

import type { Repository } from "@/lib/api"

/** `owner/repo` entry with an Add button, plus a picker over installed repos. */
export function AddRepositoryField({
  id,
  value,
  onChange,
  suggestions,
  canAdd,
  onAdd,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  suggestions: ReadonlyArray<Pick<Repository, "full_name" | "private">>
  canAdd: boolean
  onAdd: () => void
}) {
  const privateRepos = new Set(
    suggestions.filter((r) => r.private).map((r) => r.full_name)
  )
  return (
    <div className="flex flex-col gap-space-2">
      <div className="flex flex-col gap-space-2 sm:flex-row sm:items-end">
        <Input
          id={id}
          label="Add Repository"
          size="md"
          placeholder="owner/repo"
          value={value}
          onChange={onChange}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              onAdd()
            }
          }}
          className="min-w-0 sm:flex-1"
        />
        <Button
          color="primary"
          size="md"
          className="shrink-0"
          disabled={!canAdd}
          onClick={onAdd}
        >
          Add
        </Button>
      </div>
      {suggestions.length > 0 && (
        <Typeahead<string>
          aria-label="Search installed repos"
          className="w-full"
          emptyText="No matches"
          options={suggestions.map((r) => r.full_name)}
          placeholder="Search installed repos…"
          renderOption={(fullName) => (
            <>
              <span className="min-w-0 flex-1 truncate" title={fullName}>
                {fullName}
              </span>
              {privateRepos.has(fullName) && (
                <Badge color="secondary" size="xxs">
                  private
                </Badge>
              )}
            </>
          )}
          size="md"
          value={value || null}
          onChange={(next) => onChange(next ?? "")}
        />
      )}
    </div>
  )
}
