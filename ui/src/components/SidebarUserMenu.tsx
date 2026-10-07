import { useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { useState, useSyncExternalStore } from "react"
import { Avatar } from "@langchain/gtm-platform-design-system/ui/avatar"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@langchain/gtm-platform-design-system/ui/toggle-group"

import type { SessionUser } from "@/lib/api"
import type { Theme } from "@/lib/theme"
import type { Glyph } from "@/components/glyphs"
import {
  Copy,
  LogOut,
  Monitor,
  Moon,
  Palette,
  Settings2,
  Sun,
} from "@/components/glyphs"
import { api } from "@/lib/api"
import {
  getDatadogSessionLink,
  isDatadogRumInitialized,
  subscribeToDatadogInitialization,
} from "@/lib/datadog"
import { clearCachedRepos } from "@/lib/repoCache"
import { useTheme } from "@/lib/theme"

const THEME_OPTIONS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const satisfies ReadonlyArray<{ value: Theme; label: string; icon: Glyph }>

function isTheme(value: string | undefined): value is Theme {
  return value === "light" || value === "dark" || value === "system"
}

interface SidebarUserMenuProps {
  user: SessionUser
  /** The 48px icon rail: the avatar alone opens the menu. */
  collapsed?: boolean
  showSettingsLink?: boolean
}

/** The rail's identity foot: avatar, login and email, and the account menu behind them. */
export function SidebarUserMenu({
  user,
  collapsed = false,
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
      } catch (error) {
        console.warn("Datadog session link copy failed", error)
        setDatadogCopyStatus("error")
      }
    }
    window.setTimeout(() => setDatadogCopyStatus("idle"), 1500)
  }

  return (
    <Box
      data-slot="app-rail-identity"
      className={cn("shrink-0", collapsed ? "p-1" : "p-2")}
    >
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              aria-label={`Account menu for ${user.login}`}
              className={cn(
                "h-auto rounded-control",
                collapsed
                  ? "w-full justify-center p-1"
                  : "w-full justify-start gap-2 px-1.5 py-1.5"
              )}
            />
          }
        >
          <Avatar
            name={user.login}
            size="control"
            src={user.avatar_url ?? undefined}
          />
          {collapsed ? null : (
            <Stack gap="none" className="min-w-0 flex-1 text-left">
              <Box
                render={<span />}
                className="truncate text-label font-medium text-ink"
              >
                {user.login}
              </Box>
              {user.email ? (
                <Box
                  render={<span />}
                  className="truncate text-meta font-normal text-ink-subtle"
                >
                  {user.email}
                </Box>
              ) : null}
            </Stack>
          )}
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side={collapsed ? "right" : "top"}
          align="start"
          sideOffset={6}
          className="w-60"
        >
          <Inline
            data-slot="account-menu-header"
            gap="sm"
            align="center"
            className="px-2 py-2"
          >
            <Avatar
              name={user.login}
              size="control"
              src={user.avatar_url ?? undefined}
            />
            <Stack gap="none" className="min-w-0 flex-1">
              <Box
                render={<span />}
                className="truncate text-label font-medium text-ink"
              >
                {user.login}
              </Box>
              {user.email ? (
                <Box
                  render={<span />}
                  className="truncate text-meta text-ink-subtle"
                >
                  {user.email}
                </Box>
              ) : null}
            </Stack>
          </Inline>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            {showSettingsLink && (
              <DropdownMenuItem
                className="h-control"
                onClick={() => navigate({ to: "/my-settings" })}
              >
                <Icon icon={Settings2} size="sm" />
                Settings
              </DropdownMenuItem>
            )}
            <Inline
              data-slot="account-theme-row"
              gap="sm"
              align="center"
              justify="between"
              className="h-control px-1.5"
              onPointerDown={(event) => {
                // Keep the menu open while flipping theme chips.
                event.preventDefault()
              }}
            >
              <Inline gap="sm" align="center" className="min-w-0">
                <Icon icon={Palette} size="sm" className="text-ink-subtle" />
                <Box render={<span />} className="text-label text-ink">
                  Theme
                </Box>
              </Inline>
              <ToggleGroup
                aria-label="Theme"
                value={[theme]}
                onValueChange={(next) => {
                  const picked = next[0]
                  if (isTheme(picked)) setTheme(picked)
                }}
                className="min-h-0"
              >
                {THEME_OPTIONS.map((option) => (
                  <ToggleGroupItem
                    key={option.value}
                    value={option.value}
                    aria-label={option.label}
                    className="h-6 px-1.5"
                  >
                    <Icon icon={option.icon} size="sm" />
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </Inline>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          {datadogInitialized && (
            <DropdownMenuItem
              className="h-control"
              closeOnClick={false}
              onClick={() => void copyDatadogSessionLink()}
            >
              <Icon icon={Copy} size="sm" />
              {datadogCopyStatus === "copied"
                ? "Copied Datadog link"
                : datadogCopyStatus === "error"
                  ? "Couldn't copy Datadog link"
                  : "Copy Datadog link"}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem
            className="h-control"
            onClick={() => void onLogout()}
          >
            <Icon icon={LogOut} size="sm" />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </Box>
  )
}
