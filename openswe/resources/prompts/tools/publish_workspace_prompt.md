Publish a new version of the workspace's prompt, replacing the one before it.

Every thread of the workspace that opens from now on starts with it and keeps it for its whole life; threads already open keep the version they started with.
Publish once per wake, with every change you decided on, not once per memory: each version is a cache write every thread pays for again.
Publishing the text already published does nothing.

Args:
- prompt: The whole prompt, in markdown.
