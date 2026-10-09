import { useState } from "react"
import { useQuery } from "@tanstack/react-query"

import { Avatar } from "@langchain/macaw-components/Avatar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { agentsApi } from "@/features/agents/lib/api"
import type { ThreadParticipant } from "@/features/agents/lib/types"

function ParticipantAvatar({ person }: { person: ThreadParticipant }) {
  return (
    <Avatar
      size="sm"
      label={person.displayName}
      imageUrl={person.avatarUrl || undefined}
    />
  )
}

export function ThreadParticipants({ threadId }: { threadId: string }) {
  const [open, setOpen] = useState(false)
  const { data: people = [] } = useQuery({
    queryKey: ["agent-threads", threadId, "participants"],
    queryFn: () => agentsApi.threadParticipants(threadId),
  })
  if (!people.length) return null
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        onPointerEnter={() => setOpen(true)}
        aria-label={`View ${people.length} thread participant${people.length === 1 ? "" : "s"}`}
        data-no-drag=""
        className="focus-visible:outline-brand rounded-full p-1 hover:bg-surface-level-2 focus-visible:outline-2"
      >
        <span className="flex -space-x-1">
          {people.slice(0, 3).map((person) => (
            <ParticipantAvatar key={person.id} person={person} />
          ))}
          {people.length > 3 && (
            <span className="flex size-5 items-center justify-center rounded-full bg-surface-level-2 text-xs text-secondary">
              +{people.length - 3}
            </span>
          )}
        </span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64">
        <h3 className="font-medium">Participants · {people.length}</h3>
        <ul className="mt-3 max-h-64 space-y-3 overflow-y-auto">
          {people.map((person) => (
            <li key={person.id} className="flex items-center gap-2.5">
              <ParticipantAvatar person={person} />
              <div className="min-w-0">
                <p className="truncate text-sm">{person.displayName}</p>
                {person.githubLogin && (
                  <p className="truncate text-xs text-secondary">
                    @{person.githubLogin}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  )
}
