Publish a design document for this Notion task to the team's design database for review, and link it from the task. Use it when the task needs an approved design before implementation, for example because the repository's AGENTS.md requires one. The document starts in review; the task resumes in this thread with a message once every linked design is approved, so do not write code for the task until then.

- `title`: what the design is about, e.g. `Back Office: app providers and data layer`. The configured prefix (such as `Tech Design: `) is added for you.
- `markdown`: the whole document as Notion-flavored markdown. Headings, lists, tables (`<table>`), callouts, toggles and fenced code blocks, including `mermaid` diagrams, all render. When `get_notion_design_template` is available, follow its template.

Call it once per design. After it succeeds, use `comment_on_notion_task` to share the returned link, together with any questions reviewers must answer, then end your turn.
