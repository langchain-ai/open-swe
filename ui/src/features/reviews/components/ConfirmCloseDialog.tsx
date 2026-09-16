import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import type { OpenPullRequest } from "@/lib/api"
import { pullRequestKey } from "../lib/status"

export function ConfirmCloseDialog({
  pullRequests,
  onCancel,
  onConfirm,
}: {
  pullRequests: OpenPullRequest[]
  onCancel: () => void
  onConfirm: () => void
}) {
  const listed = pullRequests.slice(0, 10)
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Close {pullRequests.length} pull request
            {pullRequests.length === 1 ? "" : "s"}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            GitHub closes them without merging.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <ul className="max-h-48 overflow-y-auto font-mono text-xs">
          {listed.map((pr) => (
            <li key={pullRequestKey(pr)}>{pullRequestKey(pr)}</li>
          ))}
          {pullRequests.length > listed.length && (
            <li>+{pullRequests.length - listed.length} more</li>
          )}
        </ul>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>
            Close pull requests
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
