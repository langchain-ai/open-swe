# Incidents architecture

Incidents runs each enrolled Slack channel as one ordinary system-owned Open SWE thread, driven by the same webhook path that serves code channels. There is no incident-specific worker, queue, or scheduler.

## Flow

```text
Slack event ──► /webhooks/slack ──► agent.incidents.channels.handle_slack_event
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

run-completion webhook ──► agent.incidents.turns.handle_run_completion
```

One LangGraph run per turn. Nothing waits on another process.

## Identity and mapping

An enrolled channel maps to one agent thread at the Slack location `(channel_id, "0")`, the session timestamp code channels use. Thread metadata carries `source: incidents_agent`, `owner_type: system`, `visibility: public`, `incident_id`, and the Slack location. Runs on the thread carry `configurable.source = "incidents_agent"` and no personal identity; `agent.incidents.runtime.load_incident_session` re-verifies the saved binding before assembling the agent and refuses forged sources. Background-task completions and scheduled wakeups return to the thread as normal turns. Generic dashboard thread routes hide these threads; the Incidents dashboard is the review surface.

## Context and debounce

Every channel message, including bot alerts, is queued for the thread as a context block with a citable `slack:<ts>` header, its permalink, and redacted text; the standard queue middleware drains it before the next model call. A plain message schedules one automatic turn with `after_seconds=15` unless a run is already pending or running, so a burst becomes one turn. A mention by any channel member can be a control word (`pause`, `resume`, `complete`, `reopen`); a question can ask for the same in plain words, and the agent's `manage_incident` tool applies the control and posts the notice without cancelling its own run. On a regular Slack run in a channel that is not enrolled, `manage_incident start` enrolls that channel for a connected Open SWE account. A question dispatches an explicit turn with `multitask_strategy="interrupt"`, answered in the mention's thread when it has one, and unlocks the agent's tools, so it requires the sender to have a connected Open SWE account, like any other Open SWE mention; unlinked senders get the standard account-link prompt. Paused and completed channels still accumulate context but schedule nothing; a resume with waiting context schedules a turn.

## Records

| Namespace | Writer | Content |
|---|---|---|
| `incidents/policies` | admin API | enablement, prefix, exclusions, model, model-call limit |
| `incidents/incidents` | webhook handler, incidents API, completion hook | identity, thread id, status, reason, activity |
| `incidents/reports` | `record_incident_report` | latest report, digest, run id, last posted digest/run/time, findings activity |
| `incidents/summaries`, `incidents/history` | the report tool and the handler through `documents.py` | postmortem Markdown and curated metadata |

Each record has one writer class so concurrent turns and controls cannot lose updates. The dashboard merges the two activity lists and reports `investigating` while the thread has a pending or running run.

## Reports and Slack updates

The agent finishes each turn by calling `record_incident_report`. Claims without evidence ids from this turn's context blocks or tool results are dropped and noted as a gap. The tool stores the report and rewrites the postmortem summary on every turn, including turns that stay silent.

The channel receives one automatic message per incident: the first investigation that reaches a supported conclusion (`outcome == "findings"`), tracked in `investigation_posted`. An inconclusive first turn does not spend it, so the real investigation still lands. Afterwards automatic turns keep the stored report and the postmortem current without posting, and the channel belongs to the responders; only a question posts again, answered once per run. Automatic turns are driven by `FIRST_INVESTIGATION_REQUEST` until the investigation is published and by `AUTOMATIC_REQUEST` after that, so the flow instruction and the post gate agree on one condition.

The investigation is published in a fixed order — problem, previous occurrence, impact, cause, steps to solve — each a named section with its own character budget, and empty sections are skipped. A report with no filled sections falls back to its summary. The summary is the one-line headline the dashboard, the activity list, and the completion notice show, and it is the body of an answer to a question; it is deliberately left out of the investigation message, where it only repeated what responders had already written above. A missing recurrence check is published as missing, because an empty section would otherwise read as "this has never happened before".

The digest covers the conclusion — summary, problem, previous occurrence, impact, cause, outcome, next steps, hypotheses, questions, with citations stripped — and not the retrieved evidence, because every turn cites the newest channel message and would otherwise always look new. It keeps an answer from repeating itself inside one run. Pause and complete cancel the thread's pending and running runs and post a notice; complete includes the latest summary.

## Failure handling

A run that ends in error or timeout marks the incident `needs_attention`, records an activity entry, and posts one notice per run. A successful run with context still queued schedules another automatic turn so late alerts are not stranded. Slack retries are deduplicated with the shared event claims; a failed dispatch relies on Slack's redelivery and LangGraph's durable runs.

See [installation](INSTALLATION.md#incidents) and [local development](DEVELOPMENT.md) for setup.
