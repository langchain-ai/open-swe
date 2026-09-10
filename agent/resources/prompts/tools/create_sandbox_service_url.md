Create a browser URL for a service listening in the active LangSmith sandbox.

The service must listen on `0.0.0.0` at the specified port. The dashboard proxies the URL and
attaches the sandbox credential itself, so the link is short, never expires, and only signed-in
users who can read this thread can reach the service.

The service is served under `base_path`, so anything it serves from a root-absolute URL
(`/assets/app.js`, `/@vite/client`) is requested from the dashboard root and never reaches it.
Start dev servers with that base path — `vite --base=<base_path>`, Next.js `basePath`,
`ng build --base-href` — and their WebSockets and hot reload work through the proxy too. Static
files and JSON APIs need no configuration.
