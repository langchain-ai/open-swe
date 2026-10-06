"use client";

/*
 * Rules for PostureControl.
 *
 * Segmented Home / Agent switch — every Core 5 surface board. Decision:
 * `surface-decisions-2026-08-10.md` §2. Implemented with the Tabs primitive
 * (same segmented family as ToggleGroup). ⌘J still toggles Agent.
 */

import { Box, Stack } from "../ui/box";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import { Bot, Home } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { Tabs, TabsList, TabsTrigger } from "../ui/tabs";

const POSTURE_CONTROL_RULES: readonly string[] = [
  "The sanctioned agent toggle is this segmented Tabs control in the sidebar. A floating duplicate in a global header is forbidden (`APP_SHELL_RULES`).",
  "Two segments only: Home and Agent, each with its Icon (Home / Bot). Adding Slack or Dock as a third segment invents a posture the shell does not mount.",
  "Compose `Tabs` + `TabsList` + `TabsTrigger` — never a hand-rolled segmented track. Geometry and selected treatment already live on the primitive.",
  "This control does not own chrome pad. The rail that mounts it — AgentThreadRail `leading`, or a single `padding=\"sm\"` stack above SidebarNav — owns the inset. Never wrap it in a second `px-2` / `py-2`.",
  "An unread cue on Agent is a 6px primary dot, never a count badge. Counts belong on nav rows.",
  "⌘J toggles Agent. The control and the shortcut are one decision.",
];

type Posture = "home" | "agent";

interface PostureControlProps {
  value: Posture;
  onChange: (next: Posture) => void;
  /** Show the 6px unread dot on the Agent segment. */
  agentAttention?: boolean;
  /**
   * Icon-only stack for the 48px collapsed product rail. Full segmented control
   * otherwise.
   */
  collapsed?: boolean;
  className?: string;
}

function PostureControl({
  agentAttention = false,
  className,
  collapsed = false,
  onChange,
  value,
}: PostureControlProps) {
  if (collapsed) {
    return (
      <Stack
        data-slot="posture-control"
        data-collapsed="true"
        gap="xs"
        align="center"
        role="group"
        aria-label="Workspace posture"
        className={cn("w-full", className)}
      >
        <Button
          type="button"
          variant={value === "home" ? "secondary" : "ghost"}
          size="icon-sm"
          aria-label="Home"
          aria-pressed={value === "home"}
          onClick={() => onChange("home")}
        >
          <Icon icon={Home} size="sm" />
        </Button>
        <Button
          type="button"
          variant={value === "agent" ? "secondary" : "ghost"}
          size="icon-sm"
          aria-label="Agent"
          aria-pressed={value === "agent"}
          onClick={() => onChange("agent")}
          className="relative"
        >
          <Icon icon={Bot} size="sm" />
          {agentAttention ? (
            <Box
              aria-hidden
              className="absolute top-0.5 right-0.5 size-1.5 rounded-full bg-primary"
            />
          ) : null}
        </Button>
      </Stack>
    );
  }

  return (
    <Tabs
      data-slot="posture-control"
      value={value}
      onValueChange={(next) => {
        if (next === "home" || next === "agent") {
          onChange(next);
        }
      }}
      className={cn("w-full gap-0", className)}
    >
      <TabsList className="w-full" aria-label="Workspace posture">
        <TabsTrigger value="home">
          <Icon icon={Home} size="sm" />
          Home
        </TabsTrigger>
        <TabsTrigger value="agent" className="relative">
          <Icon icon={Bot} size="sm" />
          Agent
          {agentAttention ? (
            <Box
              aria-hidden
              className="absolute top-1 right-1.5 size-1.5 rounded-full bg-primary"
            />
          ) : null}
        </TabsTrigger>
      </TabsList>
    </Tabs>
  );
}

export { PostureControl, POSTURE_CONTROL_RULES };
export type { Posture, PostureControlProps };
