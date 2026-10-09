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
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-default px-6 pt-12 pb-4">
        <div className="min-w-0">
          <h1 className="font-heading text-xl font-medium tracking-tight">
            {title}
          </h1>
          {description && (
            <p className="mt-1.5 max-w-3xl text-xs text-secondary">
              {description}
            </p>
          )}
        </div>
        {actions && (
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        )}
      </header>
      <div className="flex min-h-0 flex-1 max-md:flex-col">
        {sidebar && (
          <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-default p-4 max-md:max-h-64 max-md:w-full max-md:border-r-0 max-md:border-b">
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
