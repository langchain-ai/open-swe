import type { PluginPermission } from "@openswe/plugin-sdk"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import type {
  InstallRequest,
  InstalledPlugin,
  PluginSource,
} from "@/features/plugins/lib/api"
import { pluginsApi } from "@/features/plugins/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { useSession } from "@/lib/session"

export const pluginKeys = {
  all: ["plugins"] as const,
  catalog: (source: PluginSource) => ["plugins", "catalog", source] as const,
  bundle: (id: string, commit: string) =>
    ["plugins", "bundle", id, commit] as const,
}

export function usePlugins() {
  const session = useSession()
  return useQuery({
    queryKey: pluginKeys.all,
    queryFn: pluginsApi.list,
    enabled: Boolean(session.data),
    staleTime: 60_000,
  })
}

export function usePlugin(name: string): {
  plugin: InstalledPlugin | undefined
  isPending: boolean
  error: Error | null
} {
  const plugins = usePlugins()
  return {
    plugin: plugins.data?.find((plugin) => plugin.name === name),
    isPending: plugins.isPending,
    error: plugins.error,
  }
}

export function usePluginCatalog(source: PluginSource | null) {
  return useQuery({
    queryKey: pluginKeys.catalog(
      source ?? { repository: "", path: "", ref: null }
    ),
    queryFn: () => pluginsApi.catalog(source!),
    enabled: source !== null,
    retry: false,
  })
}

export function usePluginBundle(plugin: InstalledPlugin) {
  return useQuery({
    queryKey: pluginKeys.bundle(plugin.id, plugin.commit_sha),
    queryFn: () => pluginsApi.bundle(plugin.id),
    staleTime: Infinity,
  })
}

export function useInstallPlugin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: InstallRequest) => pluginsApi.install(body),
    meta: { errorTitle: "Couldn't install plugin" },
    onSuccess: (installed) => {
      queryClient.setQueryData<Array<InstalledPlugin>>(
        pluginKeys.all,
        (prev) => [
          ...(prev ?? []).filter((plugin) => plugin.name !== installed.name),
          installed,
        ]
      )
      void queryClient.invalidateQueries({ queryKey: pluginKeys.all })
    },
  })
}

export function useUpgradePlugin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (vars: { id: string; permissions: Array<PluginPermission> }) =>
      pluginsApi.upgrade(vars.id, vars.permissions),
    // A request for new permissions is answered with a review, not a toast.
    meta: { silent: true },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: pluginKeys.all }),
  })
}

export function useSetPluginRepositories() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (vars: { id: string; repositories: Array<string> }) =>
      pluginsApi.setRepositories(vars.id, vars.repositories),
    meta: { errorTitle: "Couldn't update plugin repositories" },
    onMutate: async (vars) => ({
      undo: await optimisticUpdate<Array<InstalledPlugin>>(
        queryClient,
        pluginKeys.all,
        (prev) =>
          prev.map((plugin) =>
            plugin.id === vars.id
              ? { ...plugin, repositories: vars.repositories }
              : plugin
          )
      ),
    }),
    onError: (_error, _vars, context) => context?.undo(),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: pluginKeys.all }),
  })
}

export function useUninstallPlugin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => pluginsApi.uninstall(id),
    meta: { errorTitle: "Couldn't uninstall plugin" },
    onMutate: async (id) => ({
      undo: await optimisticUpdate<Array<InstalledPlugin>>(
        queryClient,
        pluginKeys.all,
        (prev) => prev.filter((plugin) => plugin.id !== id)
      ),
    }),
    onError: (_error, _vars, context) => context?.undo(),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: pluginKeys.all }),
  })
}
