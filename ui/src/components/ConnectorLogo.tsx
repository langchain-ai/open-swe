import { useState } from "react"

import type { ManagedServer } from "@/lib/api"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

function imageUrl(value?: string | null): string | undefined {
  if (!value) return
  try {
    const url = new URL(value)
    if (url.protocol === "https:" && !url.username && !url.password) {
      return url.href
    }
  } catch {
    return undefined
  }
}

export function ConnectorLogo({
  name,
  icons,
  className,
}: {
  name: string
  icons?: Pick<ManagedServer, "icon_url" | "icon_dark_url">
  className?: string
}) {
  const theme = useResolvedTheme()
  const [failed, setFailed] = useState<string[]>([])
  const light = imageUrl(icons?.icon_url)
  const dark = theme === "dark" ? imageUrl(icons?.icon_dark_url) : undefined
  const src = [dark, light].find((url) => url && !failed.includes(url))
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-md bg-surface-level-3 text-xs font-medium text-secondary uppercase select-none",
        className
      )}
    >
      {src ? (
        <img
          key={src}
          src={src}
          alt=""
          referrerPolicy="no-referrer"
          className="size-4 object-contain"
          onError={() => setFailed((urls) => [...urls, src])}
        />
      ) : (
        name.trim().slice(0, 1)
      )}
    </span>
  )
}
