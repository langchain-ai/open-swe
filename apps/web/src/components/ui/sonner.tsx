import { useTheme } from "@/components/theme-provider";
import { Toaster as Sonner, ToasterProps } from "sonner";

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = "system" } = useTheme();

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg dark:group-[.toaster]:bg-slate-950 dark:group-[.toaster]:border-slate-800 dark:group-[.toaster]:shadow-2xl dark:group-[.toaster]:shadow-black/20",
          description:
            "group-[.toast]:text-muted-foreground dark:group-[.toast]:text-slate-400",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground font-medium hover:group-[.toast]:bg-primary/90 dark:group-[.toast]:bg-slate-200 dark:group-[.toast]:text-slate-900 dark:hover:group-[.toast]:bg-slate-100",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground font-medium hover:group-[.toast]:bg-muted/80 dark:group-[.toast]:bg-slate-800 dark:group-[.toast]:text-slate-300 dark:hover:group-[.toast]:bg-slate-700",
          success:
            "group-[.toast]:bg-green-50 group-[.toast]:text-green-900 group-[.toast]:border-green-200 dark:group-[.toast]:bg-green-950/50 dark:group-[.toast]:text-green-100 dark:group-[.toast]:border-green-800/50",
          error:
            "group-[.toast]:bg-red-50 group-[.toast]:text-red-900 group-[.toast]:border-red-200 dark:group-[.toast]:bg-red-950/50 dark:group-[.toast]:text-red-100 dark:group-[.toast]:border-red-800/50",
          warning:
            "group-[.toast]:bg-yellow-50 group-[.toast]:text-yellow-900 group-[.toast]:border-yellow-200 dark:group-[.toast]:bg-yellow-950/50 dark:group-[.toast]:text-yellow-100 dark:group-[.toast]:border-yellow-800/50",
          info: "group-[.toast]:bg-blue-50 group-[.toast]:text-blue-900 group-[.toast]:border-blue-200 dark:group-[.toast]:bg-blue-950/50 dark:group-[.toast]:text-blue-100 dark:group-[.toast]:border-blue-800/50",
        },
      }}
      {...props}
    />
  );
};

export { Toaster };
