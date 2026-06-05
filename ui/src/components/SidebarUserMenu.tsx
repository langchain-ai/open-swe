import { Link, useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { IoLogOutOutline, IoSettingsOutline } from "react-icons/io5"

import type { SessionUser } from "@/lib/api"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { api } from "@/lib/api"

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
  const [open, setOpen] = useState(false)
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
    qc.setQueryData(["session"], null)
    navigate({ to: "/login" })
  }

  const initials = (user.login || "?").slice(0, 2).toUpperCase()

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-12 w-full touch-manipulation items-center gap-3 rounded-xl px-3 py-2 text-left outline-none hover:bg-sidebar-accent md:min-h-0 md:gap-2.5 md:rounded-md md:px-2 md:py-1.5"
      >
        <Avatar className="size-9 md:size-7">
          {user.avatar_url && (
            <AvatarImage src={user.avatar_url} alt={user.login} />
          )}
          <AvatarFallback>{initials}</AvatarFallback>
        </Avatar>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-sm font-medium md:text-xs">
            {user.login}
          </span>
          {user.email && (
            <span className="truncate text-xs text-muted-foreground md:text-[10px]">
              {user.email}
            </span>
          )}
        </div>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 bottom-full left-0 mb-2 overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
        >
          {showSettingsLink && (
            <Link
              to="/my-settings"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex min-h-10 w-full touch-manipulation items-center gap-2 rounded-sm px-3 py-2 text-sm hover:bg-muted md:min-h-0 md:px-2 md:py-1.5 md:text-xs/relaxed"
            >
              <IoSettingsOutline className="size-4 md:size-3.5" />
              Dashboard settings
            </Link>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => void onLogout()}
            className="flex min-h-10 w-full touch-manipulation items-center gap-2 rounded-sm px-3 py-2 text-left text-sm hover:bg-muted md:min-h-0 md:px-2 md:py-1.5 md:text-xs/relaxed"
          >
            <IoLogOutOutline className="size-4 md:size-3.5" />
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
