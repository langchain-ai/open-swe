import { useQuery } from "@tanstack/react-query"

import { ApiError, api } from "./api"
import { useSession } from "./session"

export function useModelIdentity(options: {
  workspace?: string | null
  thread?: string | null
}): boolean {
  const session = useSession()
  const workspace = options.workspace ?? undefined
  const thread = options.thread ?? undefined
  const query = useQuery({
    queryKey: ["modelIdentity", workspace ?? null, thread ?? null],
    queryFn: async () => {
      try {
        return await api.getModelIdentity({ workspace, thread })
      } catch (e) {
        if (e instanceof ApiError) return { show_model_identity: false }
        throw e
      }
    },
    enabled: !!session.data,
    staleTime: 60_000,
  })
  return !query.isError && query.data?.show_model_identity === true
}
