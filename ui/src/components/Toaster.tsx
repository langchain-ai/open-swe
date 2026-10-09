import {
  CheckIcon,
  InfoFillIcon,
  XIcon,
} from "@langchain/macaw-components/icons"
import { Icon } from "@langchain/macaw-components/Icon"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { ExclamationMarkIcon } from "@phosphor-icons/react/dist/ssr/ExclamationMark"
import { Toaster as Sonner, type ToasterProps } from "sonner"

import { useResolvedTheme } from "@/lib/theme"

/** Sonner's imperative toasts, dressed as Macaw's Toast. */
export function Toaster(props: ToasterProps) {
  const theme = useResolvedTheme()

  return (
    <Sonner
      theme={theme}
      icons={{
        success: <Icon icon={CheckIcon} size="sm" rounded color="success" />,
        info: <Icon icon={InfoFillIcon} size="sm" rounded color="info" />,
        warning: (
          <Icon
            icon={ExclamationMarkIcon}
            weight="regular"
            size="sm"
            rounded
            color="warning"
          />
        ),
        error: (
          <Icon
            icon={ExclamationMarkIcon}
            weight="regular"
            size="sm"
            rounded
            color="error"
          />
        ),
        loading: <Spinner size="sm" />,
        close: <XIcon size={14} weight="regular" />,
      }}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast:
            "flex w-[var(--width)] items-start gap-space-2 rounded-md border p-space-3 text-primary shadow-lg",
          default: "border-subtle bg-surface-level-2",
          loading: "border-subtle bg-surface-level-2",
          info: "border-brand-subtle bg-brand-subtle",
          success: "border-success bg-success",
          warning: "border-warning bg-warning",
          error: "border-error bg-error",
          icon: "flex shrink-0 items-center",
          content: "flex min-w-0 flex-1 flex-col gap-space-1 self-center",
          title: "select-text text-sm font-medium text-primary",
          description:
            "max-h-32 select-text overflow-y-auto whitespace-pre-wrap text-xxs text-secondary",
          actionButton:
            "shrink-0 self-center rounded-md border border-default bg-surface-level-1 px-space-2 py-space-1 text-xs font-medium text-primary hover:bg-surface-level-1-hover",
          cancelButton:
            "shrink-0 self-center rounded-md px-space-2 py-space-1 text-xs font-medium text-secondary hover:bg-surface-level-1-hover",
          closeButton:
            "order-last flex size-6 shrink-0 items-center justify-center rounded-sm text-icon-secondary hover:bg-surface-level-1-hover hover:text-icon-primary",
        },
      }}
      {...props}
    />
  )
}
