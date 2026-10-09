import { Avatar } from "@langchain/macaw-components/Avatar"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { DesktopIcon } from "@phosphor-icons/react/dist/ssr/Desktop"
import { GearIcon } from "@phosphor-icons/react/dist/ssr/Gear"
import { MoonIcon } from "@phosphor-icons/react/dist/ssr/Moon"
import { SignOutIcon } from "@phosphor-icons/react/dist/ssr/SignOut"
import { SunIcon } from "@phosphor-icons/react/dist/ssr/Sun"
import { Link, useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState, useSyncExternalStore } from "react"

import type { SessionUser } from "@/lib/api"
import type { Theme } from "@/lib/theme"
import { api } from "@/lib/api"
import {
  getDatadogSessionLink,
  isDatadogRumInitialized,
  subscribeToDatadogInitialization,
} from "@/lib/datadog"
import { clearCachedRepos } from "@/lib/repoCache"
import { useTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

const THEME_OPTIONS: Array<{
  value: Theme
  label: string
  icon: IconComponent
}> = [
  { value: "light", label: "Light", icon: SunIcon },
  { value: "dark", label: "Dark", icon: MoonIcon },
  { value: "system", label: "System", icon: DesktopIcon },
]

interface SidebarUserMenuProps {
  user: SessionUser
  showSettingsLink?: boolean
}

export function SidebarUserMenu({
  user,
  showSettingsLink = false,
}: SidebarUserMenuProps) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { theme, setTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const [datadogCopyStatus, setDatadogCopyStatus] = useState<
    "idle" | "copied" | "error"
  >("idle")
  const datadogInitialized = useSyncExternalStore(
    subscribeToDatadogInitialization,
    isDatadogRumInitialized,
    () => false
  )
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("mousedown", onClickOutside)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onClickOutside)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  const onLogout = async () => {
    setOpen(false)
    await api.logout()
    clearCachedRepos()
    qc.setQueryData(["session"], null)
    navigate({ to: "/login" })
  }

  const copyDatadogSessionLink = async () => {
    const currentLink = getDatadogSessionLink()
    if (!currentLink) {
      setDatadogCopyStatus("error")
    } else {
      try {
        if (window.openSweDesktop) {
          await window.openSweDesktop.writeClipboard(currentLink)
        } else {
          await navigator.clipboard.writeText(currentLink)
        }
        setDatadogCopyStatus("copied")
      } catch {
        setDatadogCopyStatus("error")
      }
    }
    window.setTimeout(() => setDatadogCopyStatus("idle"), 1500)
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left outline-none hover:bg-surface-level-2-hover"
      >
        <Avatar
          className="size-7 text-xs"
          imageUrl={user.avatar_url ?? undefined}
          label={user.login || "?"}
          shape="circle"
        />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-xs font-medium">{user.login}</span>
          {user.email && (
            <span className="truncate text-xxs text-secondary">
              {user.email}
            </span>
          )}
        </div>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 bottom-full left-0 mb-2 overflow-hidden rounded-md border border-default bg-elevated p-1 text-primary shadow-md"
        >
          <div className="px-2 py-1.5">
            <span className="text-xxs font-medium tracking-wide text-tertiary uppercase">
              Theme
            </span>
            <div className="mt-1.5 grid grid-cols-3 gap-1">
              {THEME_OPTIONS.map((option) => {
                const Icon = option.icon
                const active = theme === option.value
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setTheme(option.value)}
                    aria-pressed={active}
                    className={cn(
                      "flex flex-col items-center gap-space-1 rounded-sm border px-space-1 py-1.5 text-xxs transition-colors",
                      active
                        ? "border-brand-subtle bg-brand-subtle text-brand-primary"
                        : "border-transparent text-secondary hover:bg-elevated-hover"
                    )}
                  >
                    <Icon size={14} weight="regular" />
                    {option.label}
                  </button>
                )
              })}
            </div>
          </div>
          <div className="my-space-1 border-t border-default" />
          {datadogInitialized && (
            <button
              type="button"
              role="menuitem"
              onClick={() => void copyDatadogSessionLink()}
              className="flex w-full items-center gap-space-2 rounded-sm px-2 py-1.5 text-left text-xs/relaxed hover:bg-elevated-hover"
            >
              <CopyIcon size={14} weight="regular" />
              {datadogCopyStatus === "copied"
                ? "Copied Datadog link"
                : datadogCopyStatus === "error"
                  ? "Couldn't copy Datadog link"
                  : "Copy Datadog link"}
            </button>
          )}
          {showSettingsLink && (
            <Link
              to="/my-settings"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex w-full items-center gap-space-2 rounded-sm px-2 py-1.5 text-xs/relaxed hover:bg-elevated-hover"
            >
              <GearIcon size={14} weight="regular" />
              Settings
            </Link>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => void onLogout()}
            className="flex w-full items-center gap-space-2 rounded-sm px-2 py-1.5 text-left text-xs/relaxed hover:bg-elevated-hover"
          >
            <SignOutIcon size={14} weight="regular" />
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
