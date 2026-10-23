/**
 * The continuation a recovery chain sends: one hidden input message on the
 * thread's own run endpoint.
 *
 * It is a normal `POST /threads/<id>/runs` — the backend needs real history for
 * the model to see the instruction — but its envelope marks it `kind="system"`
 * with a system sender, the same shape the queue middleware uses for injected
 * messages. That keeps it out of user-turn logic, the transcript, and the title
 * while still reaching the model.
 */

const RESUME_SENDER = "system:network-resume";

function xmlText(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function networkResumeMessage(prompt, threadId, generation) {
  return {
    role: "user",
    id: `network-resume:${threadId}:${generation}`,
    content: `<input-message sender="${RESUME_SENDER}" surface="desktop" kind="system">\n<content>${xmlText(prompt)}</content>\n</input-message>`,
  };
}

/** Mirror of the renderer's model configurable for a local thread. */
function localThreadConfigurable(thread) {
  const configurable = {
    source: "desktop",
    local_project_path: thread.worktreePath ?? thread.cwd,
  };
  if (!thread.modelId || !thread.effort)
    return { ...configurable, model_selection: "auto" };
  return {
    ...configurable,
    agent_model_id: thread.modelId,
    agent_effort: thread.effort,
    model_selection: "explicit",
  };
}

function runPayload({ thread, prompt, resume }) {
  return {
    assistant_id: "agent",
    // A backend that died mid-run leaves the thread with pending tasks, which
    // the server reports as busy forever. The default `enqueue` would park the
    // continuation behind a run that no longer exists and it would never start;
    // `interrupt` drops that dead step and runs the continuation instead.
    multitask_strategy: "interrupt",
    input: {
      messages: [
        networkResumeMessage(prompt, resume.threadId, resume.generation),
      ],
    },
    config: { configurable: localThreadConfigurable(thread) },
    metadata: {
      open_swe: {
        network_recovery: {
          rootRunId: resume.rootRunId,
          previousRunId: resume.previousRunId,
          generation: resume.generation,
          reason: resume.reason,
          isMeta: true,
        },
      },
    },
  };
}

/**
 * @returns {Promise<{status: "already-running"} | {status: "dispatched", runId: string | null}>}
 * @throws when the backend rejects the run, so the chain can back off.
 */
async function resumeNetworkRun({ supervisor, thread, readPrompt, resume }) {
  // A surviving run needs no continuation: re-attaching to it is enough, and
  // the prompt is never read in that case.
  const activity = await supervisor.threadActivity();
  if (activity && activity[resume.threadId] === "running")
    return { status: "already-running" };

  const pathName = `/threads/${encodeURIComponent(resume.threadId)}/runs`;
  const post = () =>
    supervisor.request(pathName, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(runPayload({ thread, prompt: readPrompt(), resume })),
    });

  let response = await post();
  if (response.status === 404) {
    // The crash took the thread record with it. Re-creating it under the same
    // id reuses whatever the checkpointer still holds, so the continuation
    // sees the conversation instead of starting from nothing.
    await supervisor.request("/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ thread_id: resume.threadId }),
    });
    response = await post();
  }
  if (!response.ok) {
    throw new Error(`Network resume run was rejected (${response.status})`);
  }
  const run = await response.json().catch(() => null);
  return {
    status: "dispatched",
    runId: typeof run?.run_id === "string" ? run.run_id : null,
  };
}

module.exports = {
  RESUME_SENDER,
  localThreadConfigurable,
  networkResumeMessage,
  resumeNetworkRun,
};
