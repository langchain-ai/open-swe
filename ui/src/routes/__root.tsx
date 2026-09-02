import {
  HeadContent,
  Outlet,
  Scripts,
  createRootRouteWithContext,
  useRouter,
  useRouterState,
} from "@tanstack/react-router"
import { TanStackRouterDevtoolsPanel } from "@tanstack/react-router-devtools"
import { TanStackDevtools } from "@tanstack/react-devtools"
import { QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"

import appCss from "../styles.css?url"
import type { QueryClient } from "@tanstack/react-query"
import { AppCommandProvider } from "@/lib/appCommands"
import { resolveSessionOnServer } from "@/lib/session-ssr"
import { ThemeSync } from "@/lib/ThemeSync"
import { apiWarmupScript } from "@/features/agents/lib/apiWarmup"

const themeInitScript = `(function(){try{var t=localStorage.getItem("open-swe-theme");var d=t==="dark"||((!t||t==="system")&&window.matchMedia("(prefers-color-scheme: dark)").matches);var r=document.documentElement;r.classList.toggle("dark",d);r.style.colorScheme=d?"dark":"light";}catch(e){}})();`

export const Route = createRootRouteWithContext<{
  queryClient: QueryClient
}>()({
  beforeLoad: ({ context, location }) =>
    resolveSessionOnServer(context.queryClient, location.href),
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      {
        name: "viewport",
        content: "width=device-width, initial-scale=1, maximum-scale=1",
      },
      { name: "theme-color", content: "#181818" },
      { title: "Open SWE" },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "manifest", href: "/manifest.webmanifest" },
      { rel: "icon", type: "image/png", href: "/favicon.png" },
      { rel: "apple-touch-icon", href: "/apple-touch-icon.png" },
    ],
  }),
  notFoundComponent: () => (
    <main className="container mx-auto p-4 pt-16">
      <h1 className="text-2xl font-medium">404</h1>
      <p className="text-muted-foreground">
        The requested page could not be found.
      </p>
    </main>
  ),
  shellComponent: RootDocument,
})

function RootDocument({ children }: { children: React.ReactNode }) {
  const { queryClient } = useRouter().options.context
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const warmupScript = apiWarmupScript(pathname)
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        {warmupScript && (
          <script dangerouslySetInnerHTML={{ __html: warmupScript }} />
        )}
        <HeadContent />
      </head>
      <body>
        <ThemeSync />
        <QueryClientProvider client={queryClient}>
          <AppCommandProvider>{children ?? <Outlet />}</AppCommandProvider>
          {import.meta.env.VITE_DEVTOOLS !== "false" && (
            <>
              <TanStackDevtools
                config={{ position: "bottom-right" }}
                plugins={[
                  {
                    name: "Tanstack Router",
                    render: <TanStackRouterDevtoolsPanel />,
                  },
                ]}
              />
              <ReactQueryDevtools initialIsOpen={false} />
            </>
          )}
        </QueryClientProvider>
        <Scripts />
      </body>
    </html>
  )
}
