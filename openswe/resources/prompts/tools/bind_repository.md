Bind one repository to Robin's integrations and set its federated workflow-start permission.
Requires an authorized admin; supported writes also work in verified sole-writer shared threads, returning only an acknowledgement.

- `repo`: GitHub repository as `owner/name`
- `may_start_threads`: false by default; pass true only when the user explicitly requests permission for this repository's GitHub Actions workflows to start threads

Binding alone does not authorize federated starts or grant repository access.
This does not preload a checkout, build an image, select settings, or change sandbox credentials.
