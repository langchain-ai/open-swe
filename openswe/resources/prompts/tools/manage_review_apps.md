Read, add or remove the apps whose review step pull request reviews follow, at the instance level or for a workspace.
Requires a currently authorized workspace admin.
`read` requires a private admin surface; `add` and `remove` also work in a verified sole-writer shared thread, returning only an acknowledgement.
Authorization is rechecked on every call.
Use only for an explicit request to change which apps reviews work for.

- `action`: `read`, `add` or `remove`.
- `app`: for `add` and `remove`, the app's id, as Settings → Apps or `GET /dashboard/api/plugins/apps` shows it.
  Only an app whose newest release declares a review step can be added.
  Reviews follow the release that was newest when the app was added, so its owner's later releases change no review; adding it again moves reviews to its newest release, and `read` shows when a newer one is out.
- `workspace`: optional slug.
  Omit to change the instance's list, which every workspace without its own inherits.
  Adding or removing for a workspace starts its own list from what it inherits.

A review follows these apps only on private repositories.
It then writes only the collections each app's review step names, under `pulls/<number>/` of the reviewed pull request, and gets a browser when an app asks for one.
