import {
  GearRegularIcon,
  MoonRegularIcon,
  SignOutRegularIcon,
  SunRegularIcon,
} from "@langchain/macaw-components/icons"
import { Avatar } from "@langchain/macaw-components/Avatar"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { DesktopIcon } from "@phosphor-icons/react/dist/ssr/Desktop"
import { Link, useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState, useSyncExternalStore } from "react"
import { BugIcon } from "@phosphor-icons/react/dist/ssr/Bug"
import { Button } from "@langchain/macaw-components/Button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"

import { CopyDiagnosticsButton } from "@/components/CopyDiagnosticsButton"
import { buildEnvironmentDiagnostics } from "@/lib/environment-diagnostics"
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
  { value: "light", label: "Light", icon: SunRegularIcon },
  { value: "dark", label: "Dark", icon: MoonRegularIcon },
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
  const [reportOpen, setReportOpen] = useState(false)
  const [desktopVersion, setDesktopVersion] = useState<string>()

  useEffect(() => {
    if (reportOpen) {
      void window.openSweDesktop?.getVersion().then(setDesktopVersion)
    }
  }, [reportOpen])
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
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-space-3 rounded-md px-space-2 py-space-1 text-left outline-none hover:bg-surface-level-2-hover focus-visible:ring-2 focus-visible:ring-focus"
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
          className="absolute right-0 bottom-full left-0 mb-space-2 overflow-hidden rounded-md border border-default bg-elevated p-space-1 text-primary shadow-md"
        >
          <div className="px-space-2 py-space-1">
            <span className="text-xxs font-medium tracking-wide text-tertiary uppercase">
              Theme
            </span>
            <div className="mt-space-1 grid grid-cols-3 gap-space-1">
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
                      "flex flex-col items-center gap-space-1 rounded-sm border px-space-1 py-space-1 text-xxs transition-colors",
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
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false)
              setDatadogCopyStatus("idle")
              setReportOpen(true)
            }}
            className="flex w-full items-center gap-space-2 rounded-sm px-space-2 py-space-1 text-left text-xs/relaxed hover:bg-elevated-hover"
          >
            <BugIcon size={14} weight="regular" />
            Report an issue
          </button>
          {showSettingsLink && (
            <Link
              to="/my-settings"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex w-full items-center gap-space-2 rounded-sm px-space-2 py-space-1 text-xs/relaxed hover:bg-elevated-hover"
            >
              <GearRegularIcon size={14} />
              Settings
            </Link>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => void onLogout()}
            className="flex w-full items-center gap-space-2 rounded-sm px-space-2 py-space-1 text-left text-xs/relaxed hover:bg-elevated-hover"
          >
            <SignOutRegularIcon size={14} />
            Sign out
          </button>
        </div>
      )}
      <Dialog open={reportOpen} onOpenChange={setReportOpen}>
        <DialogContent
          childrenClassName="gap-space-4 p-space-6"
          showClose={false}
        >
          <DialogTitle>Report an issue</DialogTitle>
          <DialogDescription>
            Send the Open SWE team a description of what went wrong, what you
            expected, and any steps to reproduce it.
          </DialogDescription>
          <p className="text-xs text-secondary">
            {datadogInitialized
              ? "Copy your session link and include it in your message so the team can find your session replay."
              : "A session replay link is unavailable. You can still report the issue and copy diagnostics below."}
          </p>
          <div className="flex flex-wrap items-start gap-space-2">
            {datadogInitialized && (
              <div className="space-y-space-2 pt-space-1">
                <Button
                  variant="outlined"
                  color="secondary"
                  size="xs"
                  leftDecorator={CopyIcon}
                  aria-label="Copy session link"
                  onClick={() => void copyDatadogSessionLink()}
                >
                  Copy session link
                </Button>
                <p role="status" className="text-xs text-secondary">
                  {datadogCopyStatus === "copied"
                    ? "Session link copied. Include it in your message to the team."
                    : datadogCopyStatus === "error"
                      ? "Couldn't copy the session link. You can still report the issue without it."
                      : null}
                </p>
              </div>
            )}
            {reportOpen && (
              <CopyDiagnosticsButton
                getDiagnostics={() =>
                  buildEnvironmentDiagnostics(user, desktopVersion)
                }
              />
            )}
          </div>
          <DialogClose asChild>
            <Button variant="outlined" size="sm">
              Close
            </Button>
          </DialogClose>
        </DialogContent>
      </Dialog>
    </div>
  )
}
