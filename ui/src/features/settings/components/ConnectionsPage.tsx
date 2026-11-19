import { Text } from "@langchain/macaw-components/Text"

import type { SessionUser } from "@/lib/api"
import { ConnectionsSection } from "./ConnectionsSection"
import { ManagedToolsSection } from "./ManagedToolsSection"

export function ConnectionsPage({ user }: { user: SessionUser }) {
  return (
    <div className="min-w-0 flex-1 overflow-y-auto text-primary">
      <div className="mx-auto flex max-w-3xl flex-col gap-space-6 px-space-4 py-space-8 sm:px-space-6">
        <header className="flex flex-col gap-space-1">
          <Text variant="h1">Connections</Text>
          <Text as="p" variant="sm" color="secondary">
            Manage the accounts and tools Robin can use on your behalf.
          </Text>
        </header>
        <ConnectionsSection user={user} />
        <ManagedToolsSection />
      </div>
    </div>
  )
}
