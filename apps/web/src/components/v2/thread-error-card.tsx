import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { AlertCircle, ArrowLeft, Lock, Search } from "lucide-react";

export interface ThreadStatusError {
  message: string;
  type: "not_found" | "unauthorized";
}

interface ThreadErrorCardProps {
  error: ThreadStatusError;
  onGoBack: () => void;
}

export function ThreadErrorCard({ error, onGoBack }: ThreadErrorCardProps) {
  const getErrorIcon = () => {
    switch (error.type) {
      case "not_found":
        return (
          <Search className="h-4 w-4 text-amber-500 dark:text-amber-400" />
        );
      case "unauthorized":
        return <Lock className="h-4 w-4 text-red-500 dark:text-red-400" />;
      default:
        return (
          <AlertCircle className="h-4 w-4 text-red-500 dark:text-red-400" />
        );
    }
  };

  const getErrorTypeLabel = () => {
    switch (error.type) {
      case "not_found":
        return "Not Found";
      case "unauthorized":
        return "Unauthorized";
      default:
        return "Error";
    }
  };

  const getErrorTypeColor = () => {
    switch (error.type) {
      case "not_found":
        return "border-amber-200 text-amber-600 dark:border-amber-800 dark:text-amber-400";
      case "unauthorized":
        return "border-red-200 text-red-600 dark:border-red-800 dark:text-red-400";
      default:
        return "border-red-200 text-red-600 dark:border-red-800 dark:text-red-400";
    }
  };

  return (
    <div className="bg-background fixed inset-0 flex items-center justify-center p-4">
      <div className="bg-card w-full max-w-md rounded-lg border p-6 shadow-lg">
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            {getErrorIcon()}
            <h4 className="text-sm font-semibold">Thread Error</h4>
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground text-xs font-medium">
                Type
              </span>
              <Badge
                variant="outline"
                className={cn(getErrorTypeColor())}
              >
                {getErrorTypeLabel()}
              </Badge>
            </div>

            <Separator />

            <div className="space-y-2">
              <span className="text-muted-foreground text-xs font-medium">
                Message
              </span>
              <p className="text-sm font-medium">{error.message}</p>
            </div>
          </div>

          <div className="pt-2">
            <Button
              onClick={onGoBack}
              variant="outline"
              className="flex w-full items-center gap-2"
            >
              <ArrowLeft className="h-4 w-4" />
              Go Back
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
