### Browser Capture Verification

Before describing, reviewing, or drawing any conclusion about a page captured with `agent-browser`
(whether from a snapshot or screenshot), verify that the captured content corresponds to the route
the user requested. Check the page title or main heading and key landmarks against the requested path;
do not assume that a successful browser command means the requested page loaded.

If a specific authenticated application route instead shows a sign-in, sign-up, login, SSO, error, or
generic marketing landing surface, treat the capture as a failed navigation. State plainly that the
requested page could not be loaded because the browser was redirected to authentication, do not review
or critique the captured surface, and ask the user for a screenshot or another way to see the page.
Never imply that you can see an authenticated preview environment when the browser session is not
authenticated. Internal LangSmith preview and app hosts require authentication that the sandbox browser
does not currently have.
