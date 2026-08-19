import { forwardRef } from "react"
import type { HTMLAttributes, JSX } from "react"

import { cn } from "@/lib/utils"

interface TextProps extends HTMLAttributes<HTMLElement> {
  variant?:
    "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "md" | "sm" | "xs" | "body"
  weight?: "semibold" | "medium" | "normal"
  as?: "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "p" | "span" | "div" | "label"
  color?:
    | "primary"
    | "secondary"
    | "tertiary"
    | "quaternary"
    | "placeholder"
    | "error"
    | "success"
  children: React.ReactNode
  htmlFor?: string
}

const textVariantClasses: Record<NonNullable<TextProps["variant"]>, string> = {
  h1: "text-2xl leading-tight font-medium tracking-tighter",
  h2: "text-lg leading-tight font-medium tracking-tighter",
  h3: "text-base leading-tight font-semibold tracking-tight",
  h4: "text-sm leading-tight font-normal tracking-wide uppercase",
  h5: "text-sm leading-tight font-normal tracking-tight",
  h6: "text-sm leading-tight font-normal tracking-wide",
  md: "text-sm leading-[1.15] tracking-tighter",
  sm: "text-xs leading-tight tracking-snug",
  xs: "text-[0.75rem] leading-[1.15] tracking-normal",
  body: "text-sm leading-normal tracking-normal",
}

const Text = forwardRef<HTMLElement, TextProps>(
  (
    { variant = "body", weight, as, className, children, color, ...props },
    ref
  ) => {
    const defaultElement = {
      h1: "h1",
      h2: "h2",
      h3: "h3",
      h4: "h4",
      h5: "h5",
      h6: "h6",
      md: "span",
      sm: "span",
      xs: "span",
      body: "p",
    }[variant] as keyof JSX.IntrinsicElements
    const Component = (as ?? defaultElement) as React.ElementType
    const weights = {
      semibold: "font-semibold",
      medium: "font-medium",
      normal: "font-normal",
    }
    const colors = {
      primary: "text-ls-primary",
      secondary: "text-ls-secondary",
      tertiary: "text-ls-tertiary",
      quaternary: "text-ls-quaternary",
      placeholder: "text-ls-placeholder",
      error: "text-ls-error",
      success: "text-ls-success",
    }

    return (
      <Component
        ref={ref}
        className={cn(
          textVariantClasses[variant],
          weight ? weights[weight] : "",
          color ? colors[color] : "",
          className
        )}
        {...props}
      >
        {children}
      </Component>
    )
  }
)

Text.displayName = "Text"

export { Text, textVariantClasses }
export type { TextProps }
