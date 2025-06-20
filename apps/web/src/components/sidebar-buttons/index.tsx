import { forwardRef, ForwardedRef } from "react";
import { motion } from "framer-motion";
import {
  Settings,
  History,
  EllipsisVertical,
  ChevronRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button";
import AuthStatus from "@/components/github/auth-status";

interface SidebarButtonsProps {
  historyOpen: boolean;
  setHistoryOpen: (open: boolean) => void;
  configOpen: boolean;
  setConfigOpen: (open: boolean) => void;
  className?: string;
}

// TODO: Implement Using this component in the thread component instead of current implementation
export const SidebarButtons = forwardRef<HTMLDivElement, SidebarButtonsProps>(
  (
    { historyOpen, setHistoryOpen, configOpen, setConfigOpen, className },
    ref: ForwardedRef<HTMLDivElement>,
  ) => {
    const handleConfigClick = () => {
      setConfigOpen(true);
      setHistoryOpen(false);
    };

    const handleHistoryClick = () => {
      setHistoryOpen(true);
      setConfigOpen(false);
    };

    const closeAll = () => {
      setConfigOpen(false);
      setHistoryOpen(false);
    };

    const isSidebarOpen = historyOpen || configOpen;

    return (
      <motion.div
        ref={ref}
        className={cn(
          "fixed top-4 z-50 transition-all duration-300 ease-in-out",
          isSidebarOpen
            ? "right-[theme(spacing.80)] md:right-[37rem]"
            : "right-4",
          className,
        )}
      >
        <div className="relative flex items-center">
          <Button
            variant="outline"
            size="icon"
            className={cn(
              "bg-background hover:bg-muted rounded-full transition-all",
              isSidebarOpen ? "opacity-100 shadow-lg" : "opacity-0 shadow-none",
            )}
            onClick={() => {
              if (!isSidebarOpen) return;
              // Sidebar is open, clicking this will close any open sidebar
              closeAll();
            }}
          >
            {isSidebarOpen ? (
              <ChevronRight className="size-5" />
            ) : (
              <EllipsisVertical className="size-5" />
            )}
          </Button>

          <div
            className={cn(
              "absolute top-0 right-0 flex items-center gap-2 transition-all",
              isSidebarOpen && "right-full mr-2",
            )}
          >
            <TooltipIconButton
              tooltip="Agent Configuration"
              variant="outline"
              size="icon"
              className={cn(
                "bg-background hover:bg-muted size-9 rounded-full shadow-xs hover:cursor-pointer",
                isSidebarOpen && "shadow-lg",
              )}
              onClick={handleConfigClick}
              aria-label="Agent Configuration"
            >
              <Settings className="size-5" />
            </TooltipIconButton>
            <TooltipIconButton
              tooltip="History"
              variant="outline"
              size="icon"
              className={cn(
                "bg-background hover:bg-muted size-9 rounded-full shadow-xs hover:cursor-pointer",
                isSidebarOpen && "shadow-lg",
              )}
              onClick={handleHistoryClick}
              aria-label="History"
            >
              <History className="size-5" />
            </TooltipIconButton>
            <AuthStatus />
          </div>
        </div>
      </motion.div>
    );
  },
);

SidebarButtons.displayName = "SidebarButtons";
