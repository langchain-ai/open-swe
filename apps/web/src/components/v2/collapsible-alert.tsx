import * as React from "react";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, ChevronUp } from "lucide-react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { ErrorState } from "./types";

const alertVariants = cva(
  "relative w-full rounded-lg border px-4 py-3 text-sm grid has-[>svg]:grid-cols-[calc(var(--spacing)*4)_1fr] grid-cols-[0_1fr] has-[>svg]:gap-x-3 gap-y-0.5 items-start [&>svg]:size-4 [&>svg]:translate-y-0.5 [&>svg]:text-current",
  {
    variants: {
      variant: {
        default: "bg-card text-card-foreground border-border",
        destructive:
          "border-destructive/50 bg-destructive/10 text-destructive dark:border-red-400 dark:bg-red-950/50 dark:text-red-300 [&>svg]:text-destructive dark:[&>svg]:text-red-400",
        warning:
          "border-yellow-500/50 bg-yellow-50 text-yellow-900 dark:border-yellow-500 dark:bg-yellow-950 dark:text-yellow-100 [&>svg]:text-yellow-600 dark:[&>svg]:text-yellow-400",
        info: "border-blue-500/50 bg-blue-50 text-blue-900 dark:border-blue-500 dark:bg-blue-950 dark:text-blue-100 [&>svg]:text-blue-600 dark:[&>svg]:text-blue-400",
        success:
          "border-green-500/50 bg-green-50 text-green-900 dark:border-green-500 dark:bg-green-950 dark:text-green-100 [&>svg]:text-green-600 dark:[&>svg]:text-green-400",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

interface CollapsibleAlertProps
  extends React.ComponentProps<"div">,
    VariantProps<typeof alertVariants> {
  errorState: ErrorState;
  icon?: React.ReactNode;
  title?: string;
}

function CollapsibleAlert({
  className,
  variant,
  errorState,
  icon,
  title = "An error occurred:",
  ...props
}: CollapsibleAlertProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const hasDetails = Boolean(errorState.details);
  if (!errorState.message && !hasDetails) {
    return null;
  }

  return (
    <div
      data-slot="alert"
      data-variant={variant}
      role="alert"
      className={cn(alertVariants({ variant }), className)}
      {...props}
    >
      {icon}
      <div className="col-start-2 space-y-1">
        <div className="line-clamp-1 min-h-4 font-medium tracking-tight">
          {title}
        </div>
        <div className="text-muted-foreground data-[variant=destructive]:text-destructive/90 text-sm break-all data-[variant=info]:text-blue-800 data-[variant=success]:text-green-800 data-[variant=warning]:text-yellow-800 dark:data-[variant=destructive]:text-red-200 dark:data-[variant=info]:text-blue-200 dark:data-[variant=success]:text-green-200 dark:data-[variant=warning]:text-yellow-200 [&_p]:leading-relaxed">
          {errorState.message}
        </div>

        {hasDetails && (
          <>
            <AnimatePresence initial={false}>
              {isExpanded && (
                <motion.div
                  key="details"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2, ease: "easeInOut" }}
                  className="overflow-hidden"
                >
                  <div className="mt-2 rounded border border-current/20 bg-current/5 p-2 font-mono text-xs break-all">
                    {errorState.details}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            <motion.button
              onClick={() => setIsExpanded(!isExpanded)}
              className="flex items-center gap-1 text-xs text-current/70 transition-colors duration-200 hover:text-current"
              initial={{ scale: 1 }}
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
            >
              {isExpanded ? (
                <>
                  <ChevronUp className="size-3" />
                  Hide details
                </>
              ) : (
                <>
                  <ChevronDown className="size-3" />
                  Show details
                </>
              )}
            </motion.button>
          </>
        )}
      </div>
    </div>
  );
}

export { CollapsibleAlert };
