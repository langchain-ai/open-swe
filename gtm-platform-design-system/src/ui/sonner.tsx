"use client";

/*
 * Toaster, retokenized onto CORE 14.
 *
 * Sonner paints its own surface from four custom properties rather than from
 * classes, so this is the one place a component hands raw token values across a
 * library boundary. They are the `--gtm-` tokens themselves, which flip with
 * `[data-theme]`, so the toast follows the theme without Sonner knowing about
 * it. `--radius-panel` is emitted today, but it lives in an `@theme inline`
 * block whose values are inlined into the utilities that use them, so the
 * literal fallback covers the day nothing references the panel step any more.
 *
 * Kind is carried by the leading glyph only (positive / risk / info /
 * attention ink). The panel stays quiet; the toast is not a filled banner.
 * Mount this once in the root layout. Toasts are for transient operation
 * outcomes only. Validation, confirmation, and unreadable data stay inline
 * with the control or surface that owns them.
 */

import { useTheme } from "../host";
import { Toaster as Sonner, type ToasterProps } from "sonner";

import "./sonner.css";

import { AlertTriangle, CheckCircle, Info, Loader2, XCircle } from "./glyphs";
import { Icon } from "./icon";
import { cn } from "./cn";

const TOASTER_SURFACE = {
  "--normal-bg": "var(--gtm-panel)",
  "--normal-text": "var(--gtm-ink)",
  "--normal-border": "var(--gtm-line-strong)",
  "--border-radius": "var(--radius-panel, 12px)",
} as React.CSSProperties;

/*
 * State icons take the matching CORE 14 pair ink. The toast surface stays the
 * quiet panel chrome above; only the leading glyph says which kind landed.
 */
const TOASTER_ICONS = {
  success: <Icon icon={CheckCircle} className="text-positive" />,
  info: <Icon icon={Info} className="text-info" />,
  warning: <Icon icon={AlertTriangle} className="text-attention" />,
  error: <Icon icon={XCircle} className="text-risk" />,
  loading: <Icon icon={Loader2} className="animate-spin text-ink-subtle" />,
};

const Toaster = ({
  className,
  closeButton = true,
  position = "bottom-right",
  visibleToasts = 4,
  ...props
}: ToasterProps) => {
  const { theme = "system" } = useTheme();

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      className={cn("group", className)}
      closeButton={closeButton}
      icons={TOASTER_ICONS}
      position={position}
      style={TOASTER_SURFACE}
      visibleToasts={visibleToasts}
      {...props}
    />
  );
};

export { Toaster };
