import { Avatar } from "@langchain/macaw-components/Avatar"
import { Typeahead } from "@langchain/macaw-components/Typeahead"
import type { Person } from "@openswe/plugin-sdk"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { useState } from "react"

import { useImage } from "../lib/media"
import { usePeople, usePeopleSearch } from "../lib/queries"

const label = (person: Person | string) =>
  typeof person === "string" ? person : person.name || person.login

const login = (person: Person | string) =>
  typeof person === "string" ? person : person.login

/** Picks someone Robin knows on GitHub, by name or handle, as they type. */
export function PersonPicker({
  selected,
  onPick,
}: {
  /** The picked person's GitHub login. */
  selected: string | undefined
  onPick: (login: string | null) => void
}) {
  const [text, setText] = useState("")
  const found = usePeopleSearch(text)
  const known = usePeople(selected ? [selected] : [])
  const value = selected
    ? (known.get(selected.toLowerCase()) ?? selected)
    : null
  return (
    <div className="w-full sm:w-80">
      <Typeahead<Person>
        size="sm"
        aria-label="Person"
        placeholder="Search people by name or GitHub handle…"
        leftDecorator={
          <MagnifyingGlassIcon
            size={14}
            className="text-icon-secondary"
            aria-hidden="true"
          />
        }
        options={found.data ?? []}
        filterOptions={(options) => options}
        emptyText={found.isFetching ? "Searching…" : "No one matches"}
        value={value}
        onInputChange={setText}
        clearValueOnInputClear
        getOptionLabel={label}
        getOptionValue={login}
        renderOption={(person) => <PersonOption person={person} />}
        onChange={(next) => {
          setText("")
          onPick(next ? login(next) : null)
        }}
      />
    </div>
  )
}

function PersonOption({ person }: { person: Person }) {
  const { image } = useImage(person.avatar ?? undefined)
  const name = person.name || person.login
  return (
    <span className="flex min-w-0 items-center gap-space-2">
      <Avatar size="sm" shape="circle" label={name} imageUrl={image?.url} />
      <span className="truncate text-primary">{name}</span>
      {name.toLowerCase() !== person.login.toLowerCase() && (
        <span className="truncate text-tertiary">@{person.login}</span>
      )}
    </span>
  )
}
