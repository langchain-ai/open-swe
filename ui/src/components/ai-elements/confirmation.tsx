import { createContext, useContext, useMemo } from "react"
import type { ComponentProps, ReactNode } from "react"
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type ConfirmationState =
  | "approval-requested"
  | "approval-responded"
  | "output-available"
  | "output-denied"

export interface ConfirmationApproval {
  id: string
  approved?: boolean
  reason?: string
}

interface ConfirmationContextValue {
  approval: ConfirmationApproval
  state: ConfirmationState
}

const ConfirmationContext = createContext<ConfirmationContextValue | null>(null)

const useConfirmation = () => {
  const context = useContext(ConfirmationContext)

  if (!context) {
    throw new Error("Confirmation components must be used within Confirmation")
  }

  return context
}

export type ConfirmationProps = ComponentProps<typeof Alert> & {
  approval: ConfirmationApproval
  state: ConfirmationState
}

/**
 * A human-in-the-loop request rendered as an alert. The repo's `Alert` routes
 * children by slot (icon / title+description / action), so every
 * `Confirmation*` part below carries one of those slots.
 */
export const Confirmation = ({
  className,
  approval,
  state,
  variant = "warning",
  controlAlignment = "first-line",
  ...props
}: ConfirmationProps) => {
  const contextValue = useMemo(() => ({ approval, state }), [approval, state])

  return (
    <ConfirmationContext.Provider value={contextValue}>
      <Alert
        className={cn("bg-card", className)}
        controlAlignment={controlAlignment}
        variant={variant}
        {...props}
      />
    </ConfirmationContext.Provider>
  )
}

export type ConfirmationTitleProps = ComponentProps<typeof AlertTitle>

export const ConfirmationTitle = (props: ConfirmationTitleProps) => (
  <AlertTitle {...props} />
)

export type ConfirmationDescriptionProps = ComponentProps<
  typeof AlertDescription
>

export const ConfirmationDescription = (
  props: ConfirmationDescriptionProps
) => <AlertDescription {...props} />

export interface ConfirmationRequestProps {
  children?: ReactNode
}

/** Description shown only while the request is still pending. */
export const ConfirmationRequest = ({ children }: ConfirmationRequestProps) => {
  const { state } = useConfirmation()
  if (state !== "approval-requested") return null
  return <AlertDescription>{children}</AlertDescription>
}

/** Description shown once the request was approved. */
export const ConfirmationAccepted = ({
  children,
}: ConfirmationRequestProps) => {
  const { approval, state } = useConfirmation()
  if (!approval.approved || state === "approval-requested") return null
  return <AlertDescription>{children}</AlertDescription>
}

/** Description shown once the request was rejected. */
export const ConfirmationRejected = ({
  children,
}: ConfirmationRequestProps) => {
  const { approval, state } = useConfirmation()
  if (approval.approved !== false || state === "approval-requested") return null
  return <AlertDescription>{children}</AlertDescription>
}

export type ConfirmationActionsProps = ComponentProps<typeof AlertAction>

/** Action buttons; rendered only while the request is still pending. */
export const ConfirmationActions = ({
  className,
  ...props
}: ConfirmationActionsProps) => {
  const { state } = useConfirmation()
  if (state !== "approval-requested") return null
  return <AlertAction className={cn("flex-wrap gap-2", className)} {...props} />
}

export type ConfirmationActionProps = ComponentProps<typeof Button>

export const ConfirmationAction = (props: ConfirmationActionProps) => (
  <Button size="sm" type="button" {...props} />
)
