import { memo } from "react"
import { Streamdown } from "streamdown"
import type { ComponentProps, HTMLAttributes } from "react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export type MessageProps = HTMLAttributes<HTMLDivElement> & {
  from: "user" | "assistant" | "system"
}

export const Message = ({ className, from, ...props }: MessageProps) => (
  <div
    className={cn(
      "group/message flex w-full min-w-0 flex-col gap-1",
      from === "user" && "is-user items-end",
      from === "assistant" && "is-assistant",
      from === "system" && "is-system items-start",
      className
    )}
    data-message-from={from}
    {...props}
  />
)

export type MessageContentProps = HTMLAttributes<HTMLDivElement>

export const MessageContent = ({
  children,
  className,
  ...props
}: MessageContentProps) => (
  <div
    className={cn(
      "relative flex w-fit max-w-full min-w-0 flex-col gap-2 overflow-hidden",
      "group-[.is-user]/message:rounded-2xl group-[.is-user]/message:bg-accent group-[.is-user]/message:p-3 group-[.is-user]/message:text-accent-foreground",
      "group-[.is-system]/message:rounded-2xl group-[.is-system]/message:border group-[.is-system]/message:border-border group-[.is-system]/message:bg-muted/50 group-[.is-system]/message:p-3",
      "group-[.is-assistant]/message:w-full group-[.is-assistant]/message:text-foreground",
      className
    )}
    {...props}
  >
    {children}
  </div>
)

export type MessageActionsProps = ComponentProps<"div">

export const MessageActions = ({
  className,
  children,
  ...props
}: MessageActionsProps) => (
  <div className={cn("flex items-center gap-1", className)} {...props}>
    {children}
  </div>
)

export type MessageActionProps = ComponentProps<typeof Button> & {
  tooltip?: string
  label?: string
}

export const MessageAction = ({
  tooltip,
  children,
  label,
  variant = "ghost",
  size = "icon-xs",
  ...props
}: MessageActionProps) => {
  const button = (
    <Button size={size} type="button" variant={variant} {...props}>
      {children}
      <span className="sr-only">{label || tooltip}</span>
    </Button>
  )

  if (tooltip) {
    return (
      <Tooltip>
        <TooltipTrigger render={button} />
        <TooltipPopup>{tooltip}</TooltipPopup>
      </Tooltip>
    )
  }

  return button
}

export type MessageResponseProps = ComponentProps<typeof Streamdown>

export const MessageResponse = memo(
  ({ className, ...props }: MessageResponseProps) => (
    <Streamdown
      className={cn(
        "size-full [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        className
      )}
      {...props}
    />
  )
)

MessageResponse.displayName = "MessageResponse"

export type MessageToolbarProps = ComponentProps<"div">

export const MessageToolbar = ({
  className,
  children,
  ...props
}: MessageToolbarProps) => (
  <div
    className={cn("mt-1 flex w-full items-center gap-1", className)}
    {...props}
  >
    {children}
  </div>
)
