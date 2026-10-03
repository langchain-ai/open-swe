import { Link, useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { useState, useSyncExternalStore } from "react"
import {
  IoCopyOutline,
  IoDesktopOutline,
  IoLogOutOutline,
  IoMoonOutline,
  IoSettingsOutline,
  IoSunnyOutline,
} from "react-icons/io5"

import type { SessionUser } from "@/lib/api"
import type { Theme } from "@/lib/theme"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import {
  Menu,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuSeparator,
  MenuTrigger,
} from "@/components/ui/menu"
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
  icon: typeof IoSunnyOutline
}> = [
  { value: "light", label: "Light", icon: IoSunnyOutline },
  { value: "dark", label: "Dark", icon: IoMoonOutline },
  { value: "system", label: "System", icon: IoDesktopOutline },
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
  const [datadogCopyStatus, setDatadogCopyStatus] = useState<
    "idle" | "copied" | "error"
  >("idle")
  const datadogInitialized = useSyncExternalStore(
    subscribeToDatadogInitialization,
    isDatadogRumInitialized,
    () => false
  )
  const onLogout = async () => {
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

  const initials = (user.login || "?").slice(0, 2).toUpperCase()

  return (
    <Menu>
      <MenuTrigger className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left outline-none hover:bg-sidebar-accent data-popup-open:bg-sidebar-accent">
        <Avatar className="size-7">
          {user.avatar_url && (
            <AvatarImage src={user.avatar_url} alt={user.login} />
          )}
          <AvatarFallback>{initials}</AvatarFallback>
        </Avatar>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-xs font-medium">{user.login}</span>
          {user.email && (
            <span className="truncate text-[10px] text-muted-foreground">
              {user.email}
            </span>
          )}
        </div>
      </MenuTrigger>
      <MenuPopup align="start" className="w-(--anchor-width)" side="top">
        <MenuGroup>
          <MenuGroupLabel>Theme</MenuGroupLabel>
          <MenuRadioGroup
            onValueChange={(value: Theme) => setTheme(value)}
            value={theme}
          >
            {THEME_OPTIONS.map((option) => {
              const Icon = option.icon
              return (
                <MenuRadioItem
                  closeOnClick={false}
                  key={option.value}
                  value={option.value}
                >
                  <span className="flex items-center gap-2">
                    <Icon className="size-3.5 text-muted-foreground" />
                    {option.label}
                  </span>
                </MenuRadioItem>
              )
            })}
          </MenuRadioGroup>
        </MenuGroup>
        <MenuSeparator />
        {datadogInitialized && (
          <MenuItem
            closeOnClick={false}
            onClick={() => void copyDatadogSessionLink()}
          >
            <IoCopyOutline />
            {datadogCopyStatus === "copied"
              ? "Copied Datadog link"
              : datadogCopyStatus === "error"
                ? "Couldn't copy Datadog link"
                : "Copy Datadog link"}
          </MenuItem>
        )}
        {showSettingsLink && (
          <MenuItem render={<Link to="/my-settings" />}>
            <IoSettingsOutline />
            Settings
          </MenuItem>
        )}
        <MenuItem onClick={() => void onLogout()}>
          <IoLogOutOutline />
          Sign out
        </MenuItem>
      </MenuPopup>
    </Menu>
  )
}
