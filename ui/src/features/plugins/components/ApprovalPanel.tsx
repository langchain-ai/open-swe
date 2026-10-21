import { Button } from "@langchain/macaw-components/Button"
import { ShieldCheckIcon } from "@phosphor-icons/react/dist/ssr/ShieldCheck"

import { RepositoryPicker } from "@/features/plugins/components/RepositoryPicker"
import type { InstalledPlugin } from "@/features/plugins/lib/api"
import type { Answer, Approval } from "@/features/plugins/lib/approval"

/**
 * What a plugin asks the person, inline between the plugin's header and its
 * frame. The web UI draws it outside the frame, so the plugin can neither
 * cover nor imitate it; approving works only once it has been on screen a
 * moment (`armed`), so a click aimed at the plugin cannot land on it.
 */
export function ApprovalPanel({
  plugin,
  approval,
  armed,
  onAnswer,
}: {
  plugin: InstalledPlugin
  approval: Approval
  armed: boolean
  onAnswer: (answer: Answer | null) => void
}) {
  const name = plugin.manifest.displayName
  return (
    <section
      aria-label={`${name} is asking`}
      className="shrink-0 border-b border-default bg-surface-level-1"
      onKeyDown={(event) => {
        if (event.key === "Escape") onAnswer(null)
      }}
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-space-3 px-space-4 py-space-3">
        {approval.kind === "repositories" ? (
          <RepositoryPicker
            key={approval.pick.suggested.join(",")}
            plugin={plugin}
            pick={approval.pick}
            armed={armed}
            onAnswer={onAnswer}
          />
        ) : (
          <>
            <h2 className="flex items-center gap-space-1 text-sm font-medium text-primary">
              <ShieldCheckIcon size={14} className="text-icon-secondary" />
              {approval.kind === "thread"
                ? "Start an agent thread?"
                : "Open this link?"}
            </h2>
            {approval.kind === "thread" ? (
              <>
                <p className="text-xs text-secondary">
                  {name} wants to start a private agent thread in{" "}
                  <span className="font-medium text-primary">
                    {approval.request.repository}
                  </span>
                  . The agent works as you, with your access. Read the request
                  before you approve it.
                </p>
                <div className="flex flex-col gap-space-1">
                  <span className="text-xs font-medium text-primary">
                    {approval.request.title}
                  </span>
                  <pre className="max-h-56 overflow-auto rounded-md border border-default bg-surface-level-2 p-space-3 text-xs whitespace-pre-wrap text-primary">
                    {approval.request.request}
                  </pre>
                </div>
              </>
            ) : (
              <>
                <p className="text-xs text-secondary">
                  {name} wants to open a page outside Open SWE in a new tab.
                </p>
                <code className="rounded-md border border-default bg-surface-level-2 p-space-2 text-xs break-all text-primary">
                  {approval.url}
                </code>
              </>
            )}
            <div className="flex justify-end gap-space-2">
              <Button
                size="sm"
                variant="outlined"
                onClick={() => onAnswer(null)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={!armed}
                onClick={() => onAnswer(true)}
              >
                {approval.kind === "thread" ? "Start thread" : "Open link"}
              </Button>
            </div>
          </>
        )}
      </div>
    </section>
  )
}
