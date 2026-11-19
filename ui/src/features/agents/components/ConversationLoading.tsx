import { Skeleton, SkeletonLines } from "@/components/Skeleton"

/** A transcript-shaped placeholder: user turns on the right, replies on the left. */
export function ConversationLoading({
  label = "Loading conversation",
}: {
  label?: string
}) {
  return (
    <div
      role="status"
      aria-label={label}
      className="min-h-0 flex-1 overflow-hidden py-space-5"
    >
      <div className="mx-auto w-full max-w-3xl space-y-space-6 px-space-5">
        <div className="flex justify-end">
          <Skeleton className="h-10 w-2/5 rounded-xl" />
        </div>
        <SkeletonLines lines={4} />
        <div className="flex justify-end">
          <Skeleton className="h-16 w-1/2 rounded-xl" />
        </div>
        <SkeletonLines lines={3} />
      </div>
    </div>
  )
}
