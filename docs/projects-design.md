# Projects

Status: agreed; phase 1 in progress.

## Why

A new chat picks a project in the composer.
Today a project is only a folder on the user's Mac (`DesktopProject`, kept by `desktop/src/project-store.cts`), or none, where the agent gets its own task folder.
The sidebar groups threads by GitHub repository, so a thread in a Mac folder without a repository can only be filed by the desktop app that knows its folder, and the web sidebar hides those threads (`boundToMacFolder`).

Projects become backend objects so that:

- anyone, including people who never touch GitHub or the desktop app, can create a project on the web and start chats in it, like ChatGPT or Claude projects;
- teammates can share a project;
- the sidebar groups threads the same way on the web and on the desktop.

## Model

A project is a folder of work; a repository is a folder of work with git.

```
workspace (admin-managed: sandbox, prompt, repositories, Slack, MCP)
  └─ project (people-managed: name, members, optional repository)
       └─ thread (metadata.project_id)
```

- **Workspaces stay the team configuration layer.**
  Admins manage them; every signed-in user can pick one.
  A project belongs to one workspace, and runs started in it use that workspace.
- **Projects are made and shared by people.**
  The creator is its first owner and can add teammates as owners or members.
- **A project may be bound to one GitHub repository**, and a repository to at most one project.
  Its threads then run in that repository, and every thread in that repository is filed under the project.
  A repository with no project stays its own implicit folder, so nothing is migrated and people who never create a project see no change.
- **A thread's project is a property of the thread**, `project_id` in its metadata, set when it starts.
  Later phases put the project's instructions and files into its runs, so it has to be the thread's, not a per-viewer filing like pins.
- **A project groups; it never grants.**
  Who can read a thread is unchanged (participants and visibility): members of a shared project see the project's threads they can already read.
  Someone outside the project who can read one of its threads sees it under its repository, or "No project".
- **Paths never leave the Mac.**
  The desktop keeps `project id → folder`; the backend stores no paths.

### Tables

```sql
CREATE TABLE project (
    id            uuid PRIMARY KEY,                -- uuid7(), set in Python
    name          text NOT NULL,
    workspace_id  uuid REFERENCES workspace (id) ON DELETE SET NULL,   -- NULL = default
    repository_id uuid REFERENCES repository (id) ON DELETE SET NULL,
    created_at, updated_at, created_by, updated_by                     -- the shared shape
);
CREATE UNIQUE INDEX project_repository ON project (repository_id);

CREATE TABLE project_member (
    id         uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES project (id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('owner', 'member')),
    created_at, updated_at, created_by, updated_by
);
CREATE UNIQUE INDEX project_member_user ON project_member (project_id, user_id);
```

Owners rename, delete, bind a repository and manage members; a project always keeps at least one owner.
Members see the project and start threads in it.
Anyone who can start a thread can move their own thread into or out of a project they belong to.

### Where a thread is filed

For a viewer, each thread sits in at most one sidebar folder, first match wins:

1. its `project_id`, when the viewer is a member of that project;
2. the project bound to its repository, when the viewer is a member;
3. its repository;
4. "No project".

Deleting a project leaves its threads' `project_id` in place; an id that names no live project is treated as none.

## Phase 1

Backend:

- `project` and `project_member` tables and a `PROJECTS` store in a new `openswe/projects` package.
- Routes in `openswe/projects/routes.py`: list, create, rename, bind a repository, delete; add, change and remove members; move a thread into or out of a project.
- Agent tools for the same, except delete, which stays human-only.
  The dashboard settings map in `openswe/resources/default_prompt.md` gains projects.
- Run start: `RunConfig.project_id`, checked against the starter's memberships, written to the thread's metadata; the project's workspace and repository are used unless the run names its own.
- Listing: `list_dashboard_thread_repos` also returns the viewer's projects; `/threads/page` gains `project` (an id, or `none`), and the repository filter leaves out threads filed in one of the viewer's projects.

Desktop (needs a desktop release: store and IPC change):

- A folder with a GitHub remote keeps mapping to its repository, and so to the repository's project when it has one.
- Adding any other folder creates a project named after it, or links it to an existing one; the store keeps `projectId` beside `cwd`.
- `DesktopProject` gains `projectId`; threads started in that folder carry it.

Dashboard:

- "Choose project" (`LocalProjectSelector`) lists the viewer's projects on web and desktop, with create, rename, sharing and repository binding.
- The sidebar groups by project on both, then by repository.
- Remove the interim web hiding: `boundToMacFolder` and the sidebar's `listed` filter.

## Later

- Project instructions added to every run in the project.
- Persistent project files.
