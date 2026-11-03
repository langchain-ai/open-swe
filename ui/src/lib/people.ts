import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import type { MentionablePerson } from "@/lib/api"
import { personMentionToken } from "@/features/agents/components/composer/composerMentions"

/** People to offer for `@`, those involved in `threadId` first. */
export function useMentionablePeople(threadId?: string, enabled = true) {
  return useQuery({
    queryKey: ["mentionablePeople", threadId ?? null],
    queryFn: () => api.listMentionablePeople(threadId),
    enabled,
    staleTime: 300_000,
  })
}

/** How a person is written in a plain text box: their GitHub handle, which GitHub also understands. */
export function plainMention(person: MentionablePerson): string {
  return `@${person.login || person.name.replace(/\s+/g, "")}`
}

/** `text` with each plain `@handle` of a known person turned into their mention token, for Open SWE. */
export function tagPeople(
  text: string,
  people: ReadonlyArray<MentionablePerson>
): string {
  const byHandle = new Map(
    people.map((person) => [
      plainMention(person).slice(1).toLowerCase(),
      person,
    ])
  )
  return text.replace(
    /(^|\s)@([\w-]+)(?![\w-])/g,
    (match: string, prefix: string, handle: string) => {
      const person = byHandle.get(handle.toLowerCase())
      return person
        ? `${prefix}${personMentionToken(person.id, person.name)}`
        : match
    }
  )
}
