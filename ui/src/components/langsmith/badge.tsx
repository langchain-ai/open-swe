import { forwardRef } from "react"
import type { HTMLAttributes, ReactElement } from "react"

import type { TextProps } from "@/components/langsmith/text"
import { Text } from "@/components/langsmith/text"
import { cn } from "@/lib/utils"

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "manifestPreview"
  rounded?: "full" | "none" | "xs" | "sm"
  color?:
    | "primary"
    | "secondary"
    | "success"
    | "error"
    | "warning"
    | "special"
    | "plain"
  size?: "xxs" | "xs" | "sm" | "md"
  leftDecorator?: React.ComponentType<React.SVGProps<SVGSVGElement>>
  rightDecorator?: React.ComponentType<React.SVGProps<SVGSVGElement>>
  children?: string | ReactElement<SVGSVGElement>
  textWeight?: TextProps["weight"]
}

type BadgeSize = NonNullable<BadgeProps["size"]>

const sizeBoxClasses: Record<BadgeSize, string> = {
  xxs: "gap-0.5 px-space-1 py-px",
  xs: "gap-space-1 px-1.5 py-0.5",
  sm: "gap-space-1 px-1.5 py-0.5",
  md: "gap-space-1 px-space-2 py-space-1",
}

const sizeTextVariant: Record<BadgeSize, "xs" | "sm"> = {
  xxs: "xs",
  xs: "xs",
  sm: "sm",
  md: "sm",
}

const sizeDefaultWeight: Record<BadgeSize, NonNullable<TextProps["weight"]>> = {
  xxs: "normal",
  xs: "medium",
  sm: "medium",
  md: "medium",
}

const colorClasses: Record<NonNullable<BadgeProps["color"]>, string> = {
  primary: "bg-ls-brand-surface text-ls-brand-primary",
  secondary: "bg-ls-surface-level-3 text-ls-secondary",
  success: "bg-ls-success-surface text-ls-success",
  error: "bg-ls-error-surface text-ls-error",
  warning: "bg-ls-warning-surface text-ls-warning",
  special: "bg-ls-special-surface text-ls-special",
  plain: "border-ls-default bg-ls-surface-level-1 text-ls-primary",
}

const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  (
    {
      variant = "default",
      color,
      size,
      rounded = "full",
      leftDecorator: LeftIcon,
      rightDecorator: RightIcon,
      className,
      children,
      textWeight,
      ...props
    },
    ref
  ) => {
    const isString = typeof children === "string"
    const isManifestPreview = variant === "manifestPreview"
    const resolvedColor = color ?? (isManifestPreview ? "plain" : "secondary")
    const resolvedSize = size ?? (isManifestPreview ? "sm" : "md")
    const resolvedTextWeight = textWeight ?? sizeDefaultWeight[resolvedSize]

    return (
      <span
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center border border-transparent",
          sizeBoxClasses[resolvedSize],
          rounded === "full" ? "rounded-full" : "rounded-ls-xs",
          resolvedColor === "plain" && "border",
          colorClasses[resolvedColor],
          isManifestPreview &&
            "max-w-full min-w-0 overflow-hidden whitespace-nowrap [&>span]:truncate",
          isManifestPreview &&
            color == null &&
            "bg-transparent text-ls-secondary",
          className
        )}
        {...props}
      >
        {LeftIcon && <LeftIcon className="size-3 shrink-0" />}
        {isString ? (
          <Text
            as="span"
            variant={sizeTextVariant[resolvedSize]}
            weight={resolvedTextWeight}
          >
            {children}
          </Text>
        ) : (
          children
        )}
        {RightIcon && <RightIcon className="size-3 shrink-0" />}
      </span>
    )
  }
)

Badge.displayName = "Badge"

export { Badge }
export type { BadgeProps }
