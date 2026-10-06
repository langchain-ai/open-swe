import { useQuery } from "@tanstack/react-query"

import {
  Avatar,
  AvatarFallback,
  AvatarGroup,
  AvatarGroupCount,
  AvatarImage,
} from "@/components/ui/avatar"
import {
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { agentsApi } from "@/features/agents/lib/api"
import type { ThreadParticipant } from "@/features/agents/lib/types"

function ParticipantAvatar({ person }: { person: ThreadParticipant }) {
  return (
    <Avatar size="sm">
      {person.avatarUrl && <AvatarImage src={person.avatarUrl} alt="" />}
      <AvatarFallback>
        {person.displayName
          .split(/\s+/)
          .map((word) => word[0])
          .slice(0, 2)
          .join("")
          .toUpperCase()}
      </AvatarFallback>
    </Avatar>
  )
}

export function ThreadParticipants({ threadId }: { threadId: string }) {
  const { data: people = [] } = useQuery({
    queryKey: ["agent-threads", threadId, "participants"],
    queryFn: () => agentsApi.threadParticipants(threadId),
  })
  if (!people.length) return null
  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        aria-label={`View ${people.length} thread participant${people.length === 1 ? "" : "s"}`}
        data-no-drag=""
        className="rounded-full p-1 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
      >
        <AvatarGroup>
          {people.slice(0, 3).map((person) => (
            <ParticipantAvatar key={person.id} person={person} />
          ))}
          {people.length > 3 && (
            <AvatarGroupCount>+{people.length - 3}</AvatarGroupCount>
          )}
        </AvatarGroup>
      </PopoverTrigger>
      <PopoverPopup align="end" className="w-64">
        <PopoverTitle>Participants · {people.length}</PopoverTitle>
        <ul className="mt-3 max-h-64 space-y-3 overflow-y-auto">
          {people.map((person) => (
            <li key={person.id} className="flex items-center gap-2.5">
              <ParticipantAvatar person={person} />
              <div className="min-w-0">
                <p className="truncate text-sm">{person.displayName}</p>
                {person.githubLogin && (
                  <p className="truncate text-xs text-muted-foreground">
                    @{person.githubLogin}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      </PopoverPopup>
    </Popover>
  )
}
