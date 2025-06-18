"use client";

import { useTheme } from "@/components/theme-provider";
import { Moon, Sun } from "lucide-react";
import { TooltipIconButton } from "./ui/tooltip-icon-button";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  return (
    <TooltipIconButton
      tooltip="Toggle theme"
      variant="ghost"
      onClick={() => setTheme(theme === "light" ? "dark" : "light")}
      aria-label="Toggle theme"
    >
      {theme === "light" ? (
        <Moon className="size-4" />
      ) : (
        <Sun className="size-4" />
      )}
    </TooltipIconButton>
  );
}
