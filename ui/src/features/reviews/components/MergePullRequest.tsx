import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { CaretDownIcon } from "@phosphor-icons/react"

import { api, type MergeMethod, type OpenPullRequest } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"
import { Button } from "@/components/ui/button"
import { ButtonGroup } from "@/components/ui/button-group"
import {
  Menu,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuTrigger,
} from "@/components/ui/menu"
import { actionLabel, githubActions } from "../lib/githubActions"
import {
  mergeMethodButtonLabels,
  mergeMethodDescriptions,
  mergeMethodLabels,
  mergeMethods,
  readPreferredMergeMethod,
  writePreferredMergeMethod,
} from "../lib/mergeMethod"
import { usePullRequestAction } from "../lib/usePullRequestAction"

/** GitHub's merge button: one click merges; the caret picks how. */
export function MergePullRequest({
  pr,
  apply,
  onMerged,
}: {
  pr: OpenPullRequest
  apply: () => () => void
  onMerged?: () => void
}) {
  const [choice, setChoice] = useState<MergeMethod | null>(null)
  const [preferred] = useState(readPreferredMergeMethod)
  const allowed = useQuery({
    queryKey: ["repo-merge-methods", pr.repo],
    queryFn: () => api.repoMergeMethods(pr.repo),
    ...expiresInBrowser,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  // An unread settings list is an ambiguity, not a known impossibility, so the
  // attempt stands and GitHub's refusal is the answer.
  const options: readonly MergeMethod[] = allowed.isError
    ? mergeMethods
    : mergeMethods.filter((option) =>
        (allowed.data?.mergeMethods ?? []).includes(option)
      )
  // Never guess between allowed methods: the only one, the last one used here, or the viewer's pick.
  const method: MergeMethod | null =
    (choice && options.includes(choice) ? choice : null) ??
    (options.length === 1 ? options[0]! : null) ??
    options.find((option) => option === preferred) ??
    null
  const [menuOpen, setMenuOpen] = useState(false)
  const merge = usePullRequestAction({
    pr,
    action: "merge",
    method: method ?? undefined,
    apply,
    onDone: () => {
      if (method) writePreferredMergeMethod(method)
      onMerged?.()
    },
  })
  const busy = merge.isPending || merge.isSuccess
  const state = actionLabel(githubActions.merge.labels, merge)
  const label =
    state === githubActions.merge.labels.idle && method
      ? mergeMethodButtonLabels[method]
      : state
  return (
    <ButtonGroup aria-label={`Merge PR #${pr.number}`}>
      <Button
        size="sm"
        variant="outline"
        aria-live="polite"
        disabled={!pr.headSha || busy || allowed.isPending}
        onClick={() => (method ? merge.mutate() : setMenuOpen(true))}
      >
        {allowed.isPending ? "Merge" : method ? label : "Choose how to merge"}
      </Button>
      {options.length > 1 && (
        <Menu open={menuOpen} onOpenChange={setMenuOpen}>
          <MenuTrigger
            aria-label={`Merge method for PR #${pr.number}`}
            disabled={busy}
            render={<Button size="sm" variant="outline" className="px-1.5" />}
          >
            <CaretDownIcon />
          </MenuTrigger>
          <MenuPopup align="end" className="w-72">
            <MenuRadioGroup
              value={method}
              onValueChange={(value: MergeMethod) => setChoice(value)}
            >
              {options.map((option) => (
                <MenuRadioItem
                  key={option}
                  value={option}
                  className="items-start py-1.5"
                >
                  <span className="block font-medium">
                    {mergeMethodLabels[option]}
                  </span>
                  <span className="block text-muted-foreground">
                    {mergeMethodDescriptions[option]}
                  </span>
                </MenuRadioItem>
              ))}
            </MenuRadioGroup>
          </MenuPopup>
        </Menu>
      )}
    </ButtonGroup>
  )
}
