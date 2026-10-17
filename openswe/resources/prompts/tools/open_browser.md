Open your window of a persistent Chrome hosted by LangSmith, and return the DevTools endpoint to drive it from the sandbox. The browser keeps its logins between threads, and people can watch it live and take it over. Prefer it to a browser launched inside the sandbox when a page needs a login or a person should see what you do. Calling it again returns the same window.

- `url`: optional page to open in a new tab of your window.
- `scope`: whose browser to use. `private` is the thread owner's own browser and is the default in private threads; only that owner's private threads can open it. `workspace` is shared by every thread of this thread's workspace and is the default in workspace threads.

Drive the window from the sandbox through `cdp_url`, which works only from inside the sandbox: `agent-browser --cdp <cdp_url> ...`, or Playwright's `connect_over_cdp(cdp_url)`. Your window's tabs are yours; other threads may have windows in the same browser.

Dev servers: the browser reaches every port of this sandbox at `localhost` plus a port, for example `<localhost>:3000`, including servers bound only to 127.0.0.1, so no `--host 0.0.0.0` is needed. Plain `http://localhost:<port>` may reach no sandbox, because other sandboxes share the browser, so always use the returned address; a loopback `url` given to this tool is rewritten to it. Ports 80 and 443 cannot be reached. When the result has no `localhost`, this LangSmith deployment cannot share the sandbox's localhost yet: check dev servers with a browser launched inside the sandbox instead.

The browser holds real people's logins and cookies. Never print, copy or send cookies, tokens or session storage, and never sign out of sites.

`controller` is `user` while a person has taken the window over: your page commands then fail with "A human has control of this window" until they hand it back, so wait instead of retrying in a loop. `view_url`, when present, opens a watch-only live view without a login for 24 hours; give it to the person only when they want to watch.
