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
import { useEffect, useState, useSyncExternalStore } from "react"
import { BugIcon } from "@phosphor-icons/react/dist/ssr/Bug"
import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { DropdownMenuItem } from "@/components/MenuItem"
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
    <>
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            color="secondary"
            variant="plain"
            size="md"
            aria-label={user.login}
            className="h-auto w-full justify-start gap-space-3 py-space-1 text-left focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
          >
            <Avatar
              size="sm"
              imageUrl={user.avatar_url ?? undefined}
              label={user.login || "?"}
              shape="circle"
            />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-xs font-medium">{user.login}</span>
              {user.email && (
                <span className="truncate text-xxs text-secondary">
                  {user.email}
                </span>
              )}
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" className="min-w-56">
          <DropdownMenuLabel>Theme</DropdownMenuLabel>
          {THEME_OPTIONS.map((option) => {
            const Icon = option.icon
            return (
              <DropdownMenuItem
                key={option.value}
                role="menuitemradio"
                aria-checked={theme === option.value}
                aria-label={option.label}
                onSelect={(event) => {
                  event.preventDefault()
                  setTheme(option.value)
                }}
              >
                <Icon size={14} weight="regular" aria-hidden />
                {option.label}
                {theme === option.value && (
                  <span className="ml-auto text-xxs text-secondary">
                    Selected
                  </span>
                )}
              </DropdownMenuItem>
            )
          })}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => {
              setDatadogCopyStatus("idle")
              setReportOpen(true)
            }}
          >
            <BugIcon size={14} weight="regular" aria-hidden />
            Report an issue
          </DropdownMenuItem>
          {showSettingsLink && (
            <DropdownMenuItem asChild>
              <Link to="/my-settings">
                <GearRegularIcon size={14} aria-hidden />
                Settings
              </Link>
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => void onLogout()}>
            <SignOutRegularIcon size={14} aria-hidden />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
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
    </>
  )
}
