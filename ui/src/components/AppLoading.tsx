import { Skeleton, SkeletonLines } from "@/components/Skeleton"
import { useSidebarLayout } from "@/components/sidebar-layout"

/** The agents shell's frame, sidebar and composer, while the session loads. */
export function AppLoading() {
  const { width, collapsed } = useSidebarLayout()
  return (
    <main
      role="status"
      aria-label="Loading Robin"
      className="agents-ui flex h-svh bg-surface-level-1"
    >
      {!collapsed && (
        <div
          style={{ width }}
          className="flex shrink-0 flex-col gap-space-6 bg-surface-level-2 px-space-3 pt-space-9 max-md:hidden"
        >
          <SkeletonLines lines={4} lineClassName="h-4 w-full" />
          <SkeletonLines lines={6} lineClassName="h-4" />
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col items-center justify-center gap-space-5 px-space-5">
        <Skeleton className="h-6 w-64 max-w-full" />
        <Skeleton className="h-28 w-full max-w-3xl rounded-xl" />
      </div>
    </main>
  )
}
