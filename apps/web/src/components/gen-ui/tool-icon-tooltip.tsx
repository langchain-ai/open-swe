import { JSX } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";

export function ToolIconWithTooltip({
  toolNamePretty,
  icon,
}: {
  toolNamePretty: string;
  icon: JSX.Element;
}) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{icon}</TooltipTrigger>
        <TooltipContent>{toolNamePretty}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
