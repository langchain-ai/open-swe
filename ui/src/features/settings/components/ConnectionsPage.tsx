import { useMemo, useRef, useState } from "react"
import { Input } from "@langchain/macaw-components/Input"
import { Text } from "@langchain/macaw-components/Text"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"

import type { SessionUser } from "@/lib/api"
import { useRegisterAppCommands } from "@/lib/appCommands"
import type { AppCommand } from "@/lib/appCommands"
import { ConnectionsSection } from "./ConnectionsSection"
import { ManagedToolsSection } from "./ManagedToolsSection"

export function ConnectionsPage({ user }: { user: SessionUser }) {
  const [query, setQuery] = useState("")
  const input = useRef<HTMLInputElement>(null)
  const commands = useMemo<ReadonlyArray<AppCommand>>(
    () => [
      {
        id: "connections-search",
        label: "Search connections",
        shortcuts: ["mod+f"],
        enableInFormFields: true,
        group: "Navigation",
        menu: "edit",
        run: () => {
          input.current?.focus()
          input.current?.select()
        },
      },
    ],
    []
  )
  useRegisterAppCommands(commands)

  return (
    <div className="min-w-0 flex-1 overflow-y-auto text-primary">
      <div className="mx-auto flex max-w-3xl flex-col gap-space-6 px-space-4 py-space-8 sm:px-space-6">
        <header className="flex flex-col gap-space-1">
          <Text variant="h1">Connections</Text>
          <Text as="p" variant="sm" color="secondary">
            Manage the accounts and tools Robin can use on your behalf.
          </Text>
        </header>
        <Input
          ref={input}
          size="md"
          aria-label="Search connections"
          placeholder="Search connections…"
          leftIcon={MagnifyingGlassIcon}
          value={query}
          onChange={setQuery}
          debounceMs={0}
          rightAction={
            query
              ? {
                  icon: XIcon,
                  label: "Clear search",
                  onClick: () => {
                    setQuery("")
                    input.current?.focus()
                  },
                }
              : undefined
          }
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault()
              setQuery("")
            }
          }}
        />
        <ConnectionsSection user={user} query={query} />
        <ManagedToolsSection query={query} />
      </div>
    </div>
  )
}
