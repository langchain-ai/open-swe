import { Link } from "@tanstack/react-router";
import {
  IoArrowBackOutline,
  IoCloudOutline,
  IoGitPullRequestOutline,
  IoOptionsOutline,
  IoSettingsOutline,
  IoStatsChartOutline,
} from "react-icons/io5";
import type { ComponentType, SVGProps } from "react";

import type { SessionUser } from "@/lib/api";
import { SidebarUserMenu } from "@/components/SidebarUserMenu";
import {
  SidebarCollapseButton,
  SidebarFrame,
  useSidebarLayout,
} from "@/components/sidebar-layout";
import { cn } from "@/lib/utils";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

interface NavItem {
  to: string;
  label: string;
  icon: IconType;
  adminOnly?: boolean;
}

const NAV: Array<NavItem> = [
  { to: "/my-settings", label: "Profile Settings", icon: IoOptionsOutline },
  { to: "/cloud-agents", label: "Open SWE Agent", icon: IoCloudOutline },
  { to: "/review", label: "Open SWE Review", icon: IoGitPullRequestOutline },
  { to: "/usage", label: "Usage", icon: IoStatsChartOutline },
  { to: "/admin", label: "Admin", icon: IoSettingsOutline, adminOnly: true },
];

export function AppSidebar({ user }: { user: SessionUser }) {
  const layout = useSidebarLayout();
  return (
    <SidebarFrame {...layout} className="border-r border-border bg-sidebar text-sidebar-foreground">
      <div className="flex items-center justify-between px-4 pt-5 pb-4">
        <Link
          to="/my-settings"
          className="flex items-center gap-2 font-heading text-sm font-medium tracking-tight"
        >
          <img src="/logo-mark.png" alt="" className="size-5" />
          open-swe
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <nav className="flex flex-1 flex-col gap-0.5 px-2">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className={cn(
            "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs/relaxed text-muted-foreground transition-colors",
            "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
          )}
        >
          <IoArrowBackOutline className="size-4" />
          <span>Back to Agents</span>
        </Link>
        {NAV.filter((n) => !n.adminOnly || user.is_admin).map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.to}
              to={item.to}
              onClick={layout.closeOnMobile}
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

      <div className="p-2">
        <SidebarUserMenu user={user} />
      </div>
    </SidebarFrame>
  );
}
