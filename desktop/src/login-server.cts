const http = require("node:http");
const { createHash, randomBytes } = require("node:crypto");

const LOGIN_TIMEOUT_MS = 5 * 60 * 1000;

function page(heading, detail) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Open SWE</title>
<style>
  :root { color-scheme: light dark }
  body {
    font: 16px/1.5 system-ui, -apple-system, sans-serif;
    margin: 0; min-height: 100vh;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    text-align: center; padding: 2rem;
  }
  h1 { font-size: 1.25rem; margin: 0 0 .5rem }
  p { margin: 0; opacity: .7 }
</style>
</head>
<body>
<h1>${heading}</h1>
<p>${detail}</p>
</body>
</html>
`;
}

const SIGNED_IN_PAGE = page(
  "You're signed in",
  "You can close this tab and return to Open SWE.",
);
const FAILED_PAGE = page(
  "Sign-in failed",
  "Open SWE did not receive a sign-in code. Try again from the app.",
);
// The app still has to redeem this code, and that can fail — so this says
// what actually happened here rather than claiming the account is connected.
const CONNECTED_PAGE = page(
  "Approval received",
  "You can close this tab. Open SWE is finishing the connection.",
);
const CONNECT_FAILED_PAGE = page(
  "Connection failed",
  "Open SWE did not receive a code. Try again from the app.",
);

/**
 * Bind a loopback listener for one browser handoff, so the OAuth leg itself can
 * run in the user's own browser and hand the result back to the app.
 *
 * Resolves to the flow's PKCE material, the bound port, and a `code` promise
 * that yields the handoff code — or `null` if the flow timed out or was
 * superseded by `cancel()`.
 */
async function beginLogin({ connect = false } = {}) {
  const verifier = randomBytes(32).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");

  let resolveCode: (value: string | null) => void = () => {};
  const code = new Promise<string | null>((resolve) => {
    resolveCode = resolve;
  });

  const [okPage, failedPage] = connect
    ? [CONNECTED_PAGE, CONNECT_FAILED_PAGE]
    : [SIGNED_IN_PAGE, FAILED_PAGE];

  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    if (url.pathname !== "/callback") {
      response.writeHead(404).end();
      return;
    }
    const value = url.searchParams.get("code");
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(value ? okPage : failedPage);
    finish(value || null);
  });

  let timer = null;
  function finish(value) {
    if (timer) clearTimeout(timer);
    timer = null;
    server.closeAllConnections();
    server.close();
    resolveCode(value);
  }

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.removeListener("error", reject);
      resolve();
    });
  });

  timer = setTimeout(() => finish(null), LOGIN_TIMEOUT_MS);
  timer.unref();

  return {
    challenge,
    verifier,
    port: server.address().port,
    code,
    cancel: () => finish(null),
  };
}

module.exports = { beginLogin };
