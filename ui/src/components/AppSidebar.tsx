import { Link, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  CloudIcon,
  GearSixIcon,
  PlugsIcon,
  ShieldCheckIcon,
  SignOutIcon,
  SlidersHorizontalIcon,
} from "@phosphor-icons/react";
import type { ComponentType } from "react";

import type { SessionUser } from "@/lib/api";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type IconType = ComponentType<{ className?: string; weight?: "regular" | "fill" | "duotone" }>;

interface NavItem {
  to: string;
  label: string;
  icon: IconType;
  adminOnly?: boolean;
}

const NAV: Array<NavItem> = [
  { to: "/my-settings", label: "My Settings", icon: SlidersHorizontalIcon },
  { to: "/cloud-agents", label: "Cloud Agents", icon: CloudIcon },
  { to: "/review", label: "Open SWE Review", icon: ShieldCheckIcon },
  { to: "/integrations", label: "Integrations", icon: PlugsIcon },
  { to: "/admin", label: "Admin", icon: GearSixIcon, adminOnly: true },
];

export function AppSidebar({ user }: { user: SessionUser }) {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const onLogout = async () => {
    await api.logout();
    qc.setQueryData(["session"], null);
    navigate({ to: "/login" });
  };

  const initials = (user.login || "?").slice(0, 2).toUpperCase();

  return (
    <aside className="flex h-svh w-60 shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground">
      <div className="px-4 pt-5 pb-4">
        <Link to="/my-settings" className="font-heading text-sm font-medium tracking-tight">
          open-swe
        </Link>
      </div>

      <nav className="flex flex-1 flex-col gap-0.5 px-2">
        {NAV.filter((n) => !n.adminOnly || user.is_admin).map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.to}
              to={item.to}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs/relaxed text-muted-foreground transition-colors",
                "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              )}
              activeProps={{
                className: "bg-sidebar-accent text-sidebar-accent-foreground font-medium",
              }}
            >
              <Icon className="size-4" />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-border p-2">
        <Menu>
          <MenuTrigger
            render={(props) => (
              <button
                {...props}
                className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left outline-none hover:bg-sidebar-accent"
              />
            )}
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
          </MenuTrigger>
          <MenuContent align="end" sideOffset={8} className="min-w-[12rem]">
            <MenuItem onClick={() => void onLogout()}>
              <SignOutIcon className="size-3.5" />
              Sign out
            </MenuItem>
          </MenuContent>
        </Menu>
      </div>
    </aside>
  );
}
