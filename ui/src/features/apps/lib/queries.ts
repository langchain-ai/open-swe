import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import type { SandboxApp } from "@/lib/api"
import { api } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"

export const appKeys = {
  list: ["sandbox-apps"] as const,
  launch: ["sandbox-apps", "launch"] as const,
}

export function useSandboxApps() {
  return useQuery({
    queryKey: appKeys.list,
    queryFn: async () => (await api.listApps()).items,
  })
}

export function useLaunchSandboxApp() {
  return useMutation({
    mutationKey: appKeys.launch,
    mutationFn: api.launchApp,
    meta: { errorTitle: "Couldn't start app" },
  })
}

export function useDeleteSandboxApp() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.deleteApp,
    meta: { errorTitle: "Couldn't delete app" },
    onMutate: (id: string) =>
      optimisticUpdate<Array<SandboxApp>>(queryClient, appKeys.list, (apps) =>
        apps.filter((app) => app.id !== id)
      ),
    onError: (_error, _id, undo) => undo?.(),
    onSettled: () => queryClient.invalidateQueries({ queryKey: appKeys.list }),
  })
}
