import { toast } from "sonner"

export function copyText(text: string, copied: string): void {
  reportCopy(navigator.clipboard.writeText(text), copied)
}

export function copyPageLink(): void {
  reportCopy(
    window.openSweDesktop
      ? window.openSweDesktop.copyPageLink()
      : navigator.clipboard.writeText(window.location.href),
    "Copied the link"
  )
}

/** A shareable URL for an in-app `path`; the desktop app maps it onto its backend's origin. */
export function appUrl(path: string): Promise<string> {
  const desktop = window.openSweDesktop
  return desktop?.appUrl
    ? desktop.appUrl(path)
    : Promise.resolve(new URL(path, window.location.origin).href)
}

/** Copies shareable links to in-app `paths`, one per line. */
export function copyAppLinks(
  paths: ReadonlyArray<string>,
  copied = "Copied the link"
): void {
  reportCopy(
    Promise.all(paths.map(appUrl)).then((urls) =>
      navigator.clipboard.writeText(urls.join("\n"))
    ),
    copied
  )
}

function reportCopy(pending: Promise<void>, copied: string): void {
  pending.then(
    () => toast.success(copied),
    (error: unknown) => {
      console.warn("Could not copy to the clipboard", { error })
      toast.error("Couldn't copy to the clipboard")
    }
  )
}
