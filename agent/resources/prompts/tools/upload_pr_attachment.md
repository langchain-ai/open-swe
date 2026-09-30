Upload an image or video from the sandbox to GitHub and return a permanent attachment URL.

Use this for screenshots and recordings that belong in a pull request, issue, or comment on
`owner/repo`. The file is hosted by GitHub as a user attachment, so it never enters the repository
and keeps working after the sandbox is gone. The upload is made as the person the thread's PR is
attributed to and requires write access to the repository.

Supported: png, jpg, jpeg, gif, webp, svg (images) and mp4, mov, webm (videos), up to 10 MB.
Insert the returned `markdown` into the body you pass to `open_pull_request` or `gh pr edit`
unchanged. Image markdown renders inline; a video URL renders as a player only when it is alone in
its paragraph.
