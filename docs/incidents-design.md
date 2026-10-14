# Incidents architecture

Incidents runs each enrolled Slack channel as one ordinary system-owned Open SWE thread, driven by the same webhook path that serves code channels. The incident is that thread: its state is thread metadata, its conversation is the thread's transcript, and its report, history, and postmortem are documents beside the thread. There is no incident-specific worker, queue, scheduler, or table.

## Flow

```text
Slack event ──► /webhooks/slack ──► openswe.incidents.channels.handle_slack_event
                                     │  channel not enrolled and not an enrollment ──► regular Slack path
                                     ├─ channel_created / channel_rename with the prefix ──► enroll
                                     ├─ channel_archive ──► complete
                                     ├─ mention by an authorized responder ──► control, or explicit turn (interrupt)
                                     └─ any other message ──► queued as thread context
                                                               + one automatic turn 15 s later (enqueue)

main `agent` graph on the incident thread
  IncidentMiddleware  — incident instructions, watching/paused check, evidence from context and tools
  record_incident_report — validates citations, stores the report, updates the postmortem,
                           publishes the one automatic investigation, answers questions
  search_incidents / read_incident — retained history
  manage_incident — start (follow this channel), pause, resume, complete; also on regular Slack runs

run-completion webhook ──► openswe.incidents.turns.handle_run_completion
```

One LangGraph run per turn. Nothing waits on another process.

## Identity and mapping

An enrolled channel maps to one agent thread at the Slack location `(channel_id, "0")`, the session timestamp code channels use. Thread metadata carries `source: incidents_agent`, `owner_type: system`, `visibility: public`, the Slack location, `incident_id` (a stable id derived from the Slack workspace and channel, which dashboard links use and lookups search by), and `incident`: status, reason, channel id and name, title, anchor message, archived flag, and the runs that last failed and completed it. `openswe.incidents.threads.IncidentThreads` reads and writes that metadata. Enrollment creates the thread before it joins the channel, so even a failed setup has a thread to record it on. Runs on the thread carry `configurable.source = "incidents_agent"` and no personal identity; `openswe.incidents.runtime.load_incident_session` re-verifies the saved binding before assembling the agent and refuses forged sources. Background-task completions and scheduled wakeups return to the thread as normal turns. Generic dashboard thread routes hide these threads; the Incidents dashboard is the review surface.

## Context and debounce

Every channel message, including bot alerts, is queued for the thread as a context block with a citable `slack:<ts>` header, its permalink, and redacted text; the standard queue middleware drains it before the next model call. A plain message schedules one automatic turn with `after_seconds=15` unless a run is already pending or running, so a burst becomes one turn. A mention by any channel member can be a control word (`pause`, `resume`, `complete`, `reopen`); a question can ask for the same in plain words, and the agent's `manage_incident` tool applies the control and posts the notice without cancelling its own run. On a regular Slack run in a channel that is not enrolled, `manage_incident start` enrolls that channel for a connected Open SWE account. A question dispatches an explicit turn with `multitask_strategy="interrupt"`, answered in the mention's thread when it has one, and unlocks the agent's tools, so it requires the sender to have a connected Open SWE account, like any other Open SWE mention; unlinked senders get the standard account-link prompt. Paused and completed channels still accumulate context but schedule nothing; a resume with waiting context schedules a turn.

## Records

| Where | Writer | Content |
|---|---|---|
| instance settings record `incidents` (`settings_record`) | admin API | enablement, Slack workspace and app, prefix, exclusions, model, model-call limit, version |
| thread metadata `incident` | webhook handler, incidents API, completion hook | status, reason, channel, title, anchor, archived, failed/completed run ids |
| thread document `incident_report` | `record_incident_report` | latest report, digest, run id, last posted digest/run/time, findings activity |
| thread document `incident_history` | every incident save, and the report tool | curated metadata and the activity log |
| thread document `incident_postmortem` | the report tool through `documents.py` | postmortem Markdown |

Thread documents are rows of the generic `thread_document` table (`openswe/threads/documents.py`), one per thread and kind. The dashboard merges the two activity lists and reports `investigating` while the thread has a pending or running run.

The policy is one record beside the instance settings record rather than fields in it: one Slack installation serves the whole instance, so no workspace overrides it, and the general settings form replaces its record wholesale on every save. The admin API keeps its `version` check and server-owned Slack identity.

The transcript covers what runs did. Enrollment, controls, and failures happen outside runs, so they stay in the history document's activity log; the thread's metadata would carry the log into every thread search.

## Retention

A thread is deleted once its checkpoint retention ends (`langgraph.json`). The documents key on the thread id without a foreign key and outlive it: History and the postmortem keep working, with access decided from the history document's channel. The incident leaves the active list. The channel's next Slack event, or `manage_incident start`, brings the incident back on its thread from the history document, so an enrolled channel never falls through to the regular Slack path.

## Commands

Dashboard commands are claimed with the shared event claims (`openswe.event_claims`) for a day, so a retried request does not repeat its action, and a request id reused with different content is refused. `ask` and `investigate_again` start runs on the thread; pause, resume, complete, and reopen update its metadata and cancel runs as Slack controls do. Controls a signed-in dashboard user sends record them as the history document's `updated_by`.

## Existing data

The `incidents` Store import moves the policy into settings, each incident into its thread's metadata (giving a setup failure that never had a thread the channel's thread), and reports, history with the incident's activity, and summaries into thread documents. A Store copy stays until its copy has gone a day without a write, a Store record saved later by an old replica wins by its own `updated_at` (the policy by `version`, a postmortem by its history's), and an incident whose thread has expired keeps only its documents. Command receipts and records with no incident are dropped.

## Reports and Slack updates

The agent finishes each turn by calling `record_incident_report`. Claims without evidence ids from this turn's context blocks or tool results are dropped and noted as a gap. The tool stores the report and rewrites the postmortem summary on every turn, including turns that stay silent.

The channel receives one automatic message per incident: the first investigation that reaches a supported conclusion (`outcome == "findings"`), tracked in `investigation_posted`. An inconclusive first turn does not spend it, so the real investigation still lands. Afterwards automatic turns keep the stored report and the postmortem current without posting, and the channel belongs to the responders; only a question posts again, answered once per run. Automatic turns are driven by `FIRST_INVESTIGATION_REQUEST` until the investigation is published and by `AUTOMATIC_REQUEST` after that, so the flow instruction and the post gate agree on one condition.

The investigation is published in a fixed order — problem, previous occurrence, impact, cause, steps to solve — each a named section with its own character budget, and empty sections are skipped. A report with no filled sections falls back to its summary. The summary is the one-line headline the dashboard, the activity list, and the completion notice show, and it is the body of an answer to a question; it is deliberately left out of the investigation message, where it only repeated what responders had already written above. A missing recurrence check is published as missing, because an empty section would otherwise read as "this has never happened before".

The digest covers the conclusion — summary, problem, previous occurrence, impact, cause, outcome, next steps, hypotheses, questions, with citations stripped — and not the retrieved evidence, because every turn cites the newest channel message and would otherwise always look new. It keeps an answer from repeating itself inside one run. Pause and complete cancel the thread's pending and running runs and post a notice; complete includes the latest summary.

## What stays incident-specific

Runs are ordinary durable runs on the `agent` graph (`create_durable_run`), so they already get Managed Deep Agents routing, transcripts, and Slack posting. Three things remain incident-specific triggers rather than the regular Slack path, because a regular Slack thread only runs when someone mentions the bot: every channel message is queued as citable context, a burst of messages becomes one debounced automatic turn, and a completed run with context still queued schedules another. Generic dashboard thread routes still hide incident threads: their readability depends on the bot still being in a public channel, which those routes do not check, so the Incidents pages remain the review surface and the detail page does not embed the transcript.

## Failure handling

A run that ends in error or timeout marks the incident `needs_attention`, records an activity entry, and posts one notice per run. A successful run with context still queued schedules another automatic turn so late alerts are not stranded. Slack retries are deduplicated with the shared event claims; a failed dispatch relies on Slack's redelivery and LangGraph's durable runs.

See [installation](INSTALLATION.md#incidents) and [local development](DEVELOPMENT.md) for setup.
