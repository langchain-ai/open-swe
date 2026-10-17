Open your window of a persistent Chrome hosted by LangSmith, and return the DevTools endpoint to drive it from the sandbox. The browser keeps its logins between threads, and people can watch it live and take it over. Prefer it to a browser launched inside the sandbox when a page needs a login or a person should see what you do. Calling it again returns the same window.

- `scope` (required): whose browser to use. `user` is the thread owner's own browser, with their logins; only that owner's private threads can open it. `workspace` is one browser shared by every thread of this thread's workspace.
- `url`: optional page to open in a new tab of your window.

Drive the window from the sandbox through `cdp_url`, which works only from inside the sandbox: `agent-browser --cdp <cdp_url> ...`, or Playwright's `connect_over_cdp(cdp_url)`. Your window's tabs are yours; other threads may have windows in the same browser.

Dev servers: `localhost` in the result is this sandbox's address in the browser, `http://sb-<id>.localhost`. The browser sends every request for `sb-<id>.localhost:<port>` through a proxy in the browser's own sandbox, which forwards it over a private link into this sandbox and on to `127.0.0.1:<port>`. A dev server running here therefore serves the browser at `<localhost>:<port>` exactly as it would at `localhost:<port>` on its own machine: whether it listens on 127.0.0.1 or on all interfaces, with the same paths, WebSockets and hot reload, and as a secure context, since Chrome treats `*.localhost` as one. Start the server as usual and open `<localhost>:<port>`; a `url` given to this tool with a loopback host is opened there. Ports 80 and 443 cannot be reached.

The browser holds real people's logins and cookies. Never print, copy or send cookies, tokens or session storage, and never sign out of sites unless asked to.

`controller` is `user` while a person has taken the window over: your page commands then fail with "A human has control of this window" until they hand it back, so wait instead of retrying in a loop. `view_url`, when present, opens a watch-only live view without a login for 24 hours; give it to the person only when they want to watch.
