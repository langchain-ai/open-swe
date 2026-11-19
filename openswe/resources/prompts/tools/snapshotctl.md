Inspect the global execution image or stop its current build.
Authorized admins only.
`status` and `history` require a private admin surface; `stop` also works in verified sole-writer shared threads, returning only an acknowledgement.

Pass `action`:
- `status`: current image, build stage, and private diagnostics
- `stop`: stop the queued or running global image build
- `history`: up to 25 audited image actions from the last 30 days, newest first

Use `rebuild_execution_image(kind="full")` or `rebuild_execution_image(kind="update")` to queue a build, and `publish_execution_image()` to capture this thread's sandbox.
Inspect a returned `image-...` task with `background_task`; a queued task is not completed publication.
Image publication never stops the source sandbox.
