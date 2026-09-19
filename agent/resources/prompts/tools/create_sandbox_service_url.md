Create a browser URL for a service listening in the active LangSmith sandbox.

The service must listen on `0.0.0.0` at the specified port. It is served at the root of its own
LangSmith domain, so root-absolute URLs (`/assets/app.js`, `/@vite/client`), WebSockets, and hot
reload work with no base-path configuration.

The link carries no credential and never expires: whoever opens it signs in with their own
LangSmith session, and any member of this sandbox's LangSmith workspace gets through. The app
behind it receives their signed identity in the `X-Langsmith-User-Token` header, verifiable
against `<langsmith-url>/.well-known/jwks.json`.

Sharing one port twice returns the same link. A port that already handed out a short-lived
service token is refused until that token expires.
