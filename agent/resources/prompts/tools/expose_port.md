Expose a port of the active LangSmith sandbox on the web and return the URL that reaches it.

The service must listen on `0.0.0.0` at the specified port. It is served at the root of its own
LangSmith domain, so root-absolute URLs (`/assets/app.js`, `/@vite/client`), WebSockets, and hot
reload work with no base-path configuration.

The link carries no credential and never expires: whoever opens it signs in with their own
LangSmith session, and any member of this sandbox's LangSmith workspace gets through.

You cannot open the URL yourself: it is behind the LangSmith login, so the bot has no session for
it. Verify the service by browsing to the in-sandbox localhost with `agent-browser` instead, e.g.
`http://localhost:$$port` from inside the sandbox — never through the shared service URL.

Every request reaches the service with the viewer's identity in one header,
`X-Langsmith-User-Token`: an EdDSA-signed JWT minted per request and good for ten minutes, with
`sub` (the LangSmith user id), `email`, `name`, `iss` (the LangSmith app URL) and `aud` (the
service host). Verify it against `$jwks_url` — the keys live on the LangSmith API host, not on
the app URL the token names as its issuer. Any inbound copy of that header is
stripped first, so a verified token is the only identity a client cannot forge, and there is no
other identity header. The credential that authenticated the request never reaches the service.

Sharing one port twice returns the same link. A port that already handed out a short-lived
service token is refused until that token expires.
