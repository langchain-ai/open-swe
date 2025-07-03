"use client";

import { forwardRef } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Button, ButtonProps } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type TooltipIconButtonProps = ButtonProps & {
  tooltip: string;
  /**
   * @default "bottom"
   */
  side?: "top" | "bottom" | "left" | "right";
  /**
   * @default 200
   */
  delayDuration?: number;
  /**
   * @default "ghost"
   */
  variant?:
    | "default"
    | "destructive"
    | "outline"
    | "secondary"
    | "ghost"
    | "link"
    | "brand";
};

export const TooltipIconButton = forwardRef<
  HTMLButtonElement,
  TooltipIconButtonProps
>(
  (
    {
      children,
      tooltip,
      side = "bottom",
      className,
      delayDuration = 200,
      variant = "ghost",
      ...rest
    },
    ref,
  ) => {
    return (
      <TooltipProvider>
        <Tooltip delayDuration={delayDuration}>
          <TooltipTrigger asChild>
            <Button
              variant={variant}
              size="icon"
              {...rest}
              className={cn("size-6 p-1", className)}
              ref={ref}
            >
              {children}
              <span className="sr-only">{tooltip}</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent side={side}>{tooltip}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  },
);

TooltipIconButton.displayName = "TooltipIconButton";
