import { Link, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  IoDesktopOutline,
  IoLogOutOutline,
  IoMoonOutline,
  IoSettingsOutline,
  IoSunnyOutline,
} from "react-icons/io5";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { api, type SessionUser } from "@/lib/api";
import { useTheme, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

const THEME_OPTIONS: Array<{ value: Theme; label: string; icon: typeof IoSunnyOutline }> = [
  { value: "light", label: "Light", icon: IoSunnyOutline },
  { value: "dark", label: "Dark", icon: IoMoonOutline },
  { value: "system", label: "System", icon: IoDesktopOutline },
];

interface SidebarUserMenuProps {
  user: SessionUser;
  showSettingsLink?: boolean;
}

export function SidebarUserMenu({ user, showSettingsLink = false }: SidebarUserMenuProps) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const onLogout = async () => {
    setOpen(false);
    await api.logout();
    qc.setQueryData(["session"], null);
    navigate({ to: "/login" });
  };

  const initials = (user.login || "?").slice(0, 2).toUpperCase();

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left outline-none hover:bg-sidebar-accent"
      >
        <Avatar className="size-7">
          {user.avatar_url && <AvatarImage src={user.avatar_url} alt={user.login} />}
          <AvatarFallback>{initials}</AvatarFallback>
        </Avatar>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-xs font-medium">{user.login}</span>
          {user.email && (
            <span className="truncate text-[10px] text-muted-foreground">{user.email}</span>
          )}
        </div>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 bottom-full left-0 mb-2 overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
        >
          <div className="px-2 py-1.5">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Theme
            </span>
            <div className="mt-1.5 grid grid-cols-3 gap-1">
              {THEME_OPTIONS.map((option) => {
                const Icon = option.icon;
                const active = theme === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setTheme(option.value)}
                    aria-pressed={active}
                    className={cn(
                      "flex flex-col items-center gap-1 rounded-sm border px-1 py-1.5 text-[10px] transition-colors",
                      active
                        ? "border-primary/40 bg-primary/10 text-primary"
                        : "border-transparent text-muted-foreground hover:bg-muted",
                    )}
                  >
                    <Icon className="size-3.5" />
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="my-1 h-px bg-border" />
          {showSettingsLink && (
            <Link
              to="/my-settings"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs/relaxed hover:bg-muted"
            >
              <IoSettingsOutline className="size-3.5" />
              Dashboard settings
            </Link>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => void onLogout()}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs/relaxed hover:bg-muted"
          >
            <IoLogOutOutline className="size-3.5" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
