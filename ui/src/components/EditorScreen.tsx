import { Text } from "@langchain/macaw-components/Text"
import type { ReactNode } from "react"

interface EditorScreenProps {
  title: string
  description?: ReactNode
  /** Controls for the open document, such as status text and Save. */
  actions?: ReactNode
  /** A list of documents to pick from, shown beside the editor. */
  sidebar?: ReactNode
  children: ReactNode
}

/** A full-window editing screen: a header bar, an optional picker, and a body that fills the rest. */
export function EditorScreen({
  title,
  description,
  actions,
  sidebar,
  children,
}: EditorScreenProps) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-surface-level-1 text-primary">
      <header className="flex flex-wrap items-end justify-between gap-space-4 border-b border-default px-space-5 pt-space-8 pb-space-4">
        <div className="min-w-0">
          <Text variant="h1">{title}</Text>
          {description && (
            <Text
              as="p"
              variant="sm"
              color="secondary"
              className="mt-space-1 max-w-3xl"
            >
              {description}
            </Text>
          )}
        </div>
        {actions && (
          <div className="flex flex-wrap items-center gap-space-2">
            {actions}
          </div>
        )}
      </header>
      <div className="flex min-h-0 flex-1 max-md:flex-col">
        {sidebar && (
          <aside className="flex w-72 shrink-0 flex-col gap-space-4 overflow-y-auto border-r border-default p-space-4 max-md:max-h-64 max-md:w-full max-md:border-r-0 max-md:border-b">
            {sidebar}
          </aside>
        )}
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          {children}
        </section>
      </div>
    </div>
  )
}
