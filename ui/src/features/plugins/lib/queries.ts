import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import type {
  App,
  AppPreview,
  InstallRequest,
  InstalledPlugin,
} from "@/features/plugins/lib/api"
import { pluginsApi } from "@/features/plugins/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { optimisticUpdate } from "@/lib/optimistic"
import { useSession } from "@/lib/session"

export const pluginKeys = {
  all: ["plugins"] as const,
  apps: ["plugins", "apps"] as const,
  preview: (threadId: string) => ["plugins", "preview", threadId] as const,
  bundle: (id: string, version: string) =>
    ["plugins", "bundle", id, version] as const,
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

export function useApps(enabled = true) {
  return useQuery({
    queryKey: pluginKeys.apps,
    queryFn: pluginsApi.apps,
    enabled,
  })
}

/** The app the agent builds in ``threadId``, reloaded whenever it previews another build. */
export function useAppPreview(threadId: string) {
  return useQuery({
    queryKey: pluginKeys.preview(threadId),
    queryFn: () => pluginsApi.preview(threadId),
    meta: { invalidatedBy: [invalidationTopic("app-previews", threadId)] },
  })
}

export function useApprovePreview(threadId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: InstallRequest) =>
      pluginsApi.approvePreview(threadId, body),
    meta: { errorTitle: "Couldn't run the preview" },
    onSuccess: (preview) =>
      queryClient.setQueryData<AppPreview>(
        pluginKeys.preview(threadId),
        preview
      ),
  })
}

export function useReleasePreview(threadId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => pluginsApi.releasePreview(threadId),
    meta: { errorTitle: "Couldn't add the app to your sidebar" },
    onSuccess: (installed) => {
      queryClient.setQueryData<Array<InstalledPlugin>>(
        pluginKeys.all,
        (prev) => [
          ...(prev ?? []).filter((plugin) => plugin.id !== installed.id),
          installed,
        ]
      )
      void queryClient.invalidateQueries({ queryKey: pluginKeys.apps })
    },
  })
}

export function usePublishApp() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (html: string) => pluginsApi.publish(html),
    meta: { errorTitle: "Couldn't publish the app" },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: pluginKeys.apps }),
  })
}

export function useSetAppListed() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (vars: { id: string; listed: boolean }) =>
      pluginsApi.setListed(vars.id, vars.listed),
    meta: { errorTitle: "Couldn't change who can add the app" },
    onMutate: async (vars) => ({
      undo: await optimisticUpdate<Array<App>>(
        queryClient,
        pluginKeys.apps,
        (prev) =>
          prev.map((app) =>
            app.id === vars.id ? { ...app, listed: vars.listed } : app
          )
      ),
    }),
    onError: (_error, _vars, context) => context?.undo(),
  })
}

export function usePluginBundle(plugin: InstalledPlugin) {
  return useQuery({
    queryKey: pluginKeys.bundle(plugin.id, plugin.version.id),
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
    mutationFn: (vars: { id: string } & InstallRequest) =>
      pluginsApi.upgrade(vars.id, {
        version: vars.version,
        permissions: vars.permissions,
      }),
    // A request for new permissions is answered with a review, not a toast.
    meta: { silent: true },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: pluginKeys.all }),
  })
}

/** Puts the install's new summary, with the viewer's repositories, in the cache. */
function useReplacePlugin() {
  const queryClient = useQueryClient()
  return (updated: InstalledPlugin) =>
    queryClient.setQueryData<Array<InstalledPlugin>>(pluginKeys.all, (prev) =>
      prev?.map((plugin) => (plugin.id === updated.id ? updated : plugin))
    )
}

export function useSharePluginRepositories() {
  const replace = useReplacePlugin()
  return useMutation({
    mutationFn: (vars: { id: string; repositories: Array<string> }) =>
      pluginsApi.share(vars.id, vars.repositories),
    meta: { errorTitle: "Couldn't share the repository" },
    onSuccess: replace,
  })
}

export function useUnsharePluginRepository() {
  const queryClient = useQueryClient()
  const replace = useReplacePlugin()
  return useMutation({
    mutationFn: (vars: { id: string; repository: string }) =>
      pluginsApi.unshare(vars.id, vars.repository),
    meta: { errorTitle: "Couldn't stop sharing the repository" },
    onMutate: async (vars) => ({
      undo: await optimisticUpdate<Array<InstalledPlugin>>(
        queryClient,
        pluginKeys.all,
        (prev) =>
          prev.map((plugin) =>
            plugin.id === vars.id
              ? {
                  ...plugin,
                  repositories: plugin.repositories.filter(
                    (name) => name !== vars.repository
                  ),
                }
              : plugin
          )
      ),
    }),
    onError: (_error, _vars, context) => context?.undo(),
    onSuccess: replace,
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
