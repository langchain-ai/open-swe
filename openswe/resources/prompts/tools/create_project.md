Create a project owned by the requester.

Call this when the requester asks for a new project.
`workspace` is a workspace slug; it defaults to the instance default.
`repo_full_name` (`owner/name`) binds the project to that repository, so every thread in it files under the project; a repository belongs to at most one project, and the requester needs access to it.
