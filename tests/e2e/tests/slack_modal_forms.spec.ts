import { expect, test, type APIRequestContext } from "@playwright/test";

type FormView = {
  callback_id: string;
  private_metadata: string;
  blocks: Array<{
    block_id?: string;
    element?: { type: string; options?: Array<{ value: string }> };
  }>;
  id?: string;
  state?: { values: Record<string, Record<string, unknown>> };
};

type FormMessage = {
  ts: string;
  thread_ts: string;
  text: string;
  blocks: Array<{
    type: string;
    elements?: Array<{ action_id: string; value: string }>;
  }>;
};

async function openForm(request: APIRequestContext) {
  expect((await request.post("/control/reset")).ok()).toBeTruthy();
  const sent = await request.post("/mock/slack/send", {
    data: {
      text: "<@U0BOT> E2E_SLACK_FORM ask me to choose items",
      mention_bot: true,
    },
  });
  const opened = (await sent.json()) as {
    thread_id: string;
    thread_ts: string;
    webhook: { status: string };
  };
  expect(opened.webhook.status).toBe("accepted");
  let message: FormMessage | undefined;
  await expect
    .poll(
      async () => {
        const response = await request.get("/mock/slack/messages");
        const messages = (await response.json()) as FormMessage[];
        message = messages.find(
          (entry) =>
            entry.text.startsWith("Review items") &&
            entry.thread_ts === opened.thread_ts,
        );
        return message;
      },
      { timeout: 60_000 },
    )
    .toBeTruthy();
  const action = message!.blocks
    .flatMap((block) => block.elements ?? [])
    .find((element) => element.action_id === "open_swe_form_open");
  expect(action).toBeTruthy();
  expect(JSON.parse(action!.value)).toMatchObject({
    type: "open_swe_form",
    fingerprint: expect.any(String),
  });
  return { ...opened, message: message!, action: action! };
}

async function click(
  request: APIRequestContext,
  form: Awaited<ReturnType<typeof openForm>>,
  user = "U_ALICE",
) {
  const response = await request.post("/mock/slack/action", {
    data: {
      message_ts: form.message.ts,
      thread_ts: form.thread_ts,
      action: form.action,
      user,
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as { status?: string; reason?: string };
}

async function views(
  request: APIRequestContext,
): Promise<Array<{ trigger_id: string; view: FormView }>> {
  const response = await request.get("/mock/slack/views");
  return (await response.json()) as Array<{
    trigger_id: string;
    view: FormView;
  }>;
}

async function state(
  request: APIRequestContext,
  threadId: string,
): Promise<string> {
  const response = await request.get(`/threads/${threadId}/state`);
  if (!response.ok()) return "";
  const result = (await response.json()) as {
    values?: { messages?: Array<{ content?: unknown }> };
  };
  return (result.values?.messages ?? [])
    .map((message) => JSON.stringify(message.content))
    .join("\n");
}

test.describe("Slack modal intake forms", () => {
  test("a button posted in a code channel opens against the channel session", async ({
    request,
  }) => {
    expect((await request.post("/control/reset")).ok()).toBeTruthy();
    const created = await request.post(
      "/fake-slack/agents.conversations.create",
      {
        data: { title: "Form code channel", session_id: "form-session" },
      },
    );
    const channel = ((await created.json()) as { channel: { id: string } })
      .channel.id;
    const sent = await request.post("/mock/slack/send", {
      data: {
        channel,
        text: "E2E_SLACK_FORM_CODE ask me to choose items",
        mention_bot: false,
      },
    });
    const opened = (await sent.json()) as {
      thread_ts: string;
      thread_id: string;
      webhook: { status: string };
    };
    expect(opened.webhook.status).toBe("accepted");
    expect(opened.thread_ts).toBe("0");
    let message: FormMessage | undefined;
    await expect
      .poll(
        async () => {
          const response = await request.get(
            `/mock/slack/messages?channel=${channel}`,
          );
          const messages = (await response.json()) as FormMessage[];
          message = messages.find((entry) =>
            entry.text.startsWith("Review items"),
          );
          return message;
        },
        { timeout: 60_000 },
      )
      .toBeTruthy();
    const action = message!.blocks
      .flatMap((block) => block.elements ?? [])
      .find((element) => element.action_id === "open_swe_form_open");
    expect(action).toBeTruthy();
    const clicked = await request.post("/mock/slack/action", {
      data: {
        message_ts: message!.ts,
        thread_ts: message!.thread_ts,
        action,
        user: "U_ALICE",
      },
    });
    expect(await clicked.json()).toEqual({});
    expect(await views(request)).toHaveLength(1);
  });
  test("the tool posts a button, click opens a modal, and submission becomes an attributed turn", async ({
    request,
  }) => {
    const form = await openForm(request);
    expect(await click(request, form)).toEqual({});
    const opened = await views(request);
    expect(opened).toHaveLength(1);
    expect(opened[0].trigger_id).toMatch(/^trigger-/);
    expect(opened[0].view.callback_id).toBe("open_swe_form");
    const choices = opened[0].view.blocks.find(
      (block) => block.block_id === "choices",
    );
    expect(choices?.element?.type).toBe("checkboxes");
    expect(choices?.element?.options).toHaveLength(2);
    expect(
      opened[0].view.blocks.find((block) => block.block_id === "comment_0"),
    ).toBeTruthy();
    const view: FormView = {
      ...opened[0].view,
      id: "V1",
      state: {
        values: {
          choices: { selected: { selected_options: [{ value: "0" }] } },
          comment_0: { comment: { value: "File this as a ticket" } },
        },
      },
    };
    const submitted = await request.post("/mock/slack/submit", {
      data: { view, user: "U_ALICE" },
    });
    expect(submitted.ok()).toBeTruthy();
    expect(await submitted.json()).toEqual({});
    await expect
      .poll(() => state(request, form.thread_id), { timeout: 60_000 })
      .toContain("File this as a ticket");
    const transcript = await state(request, form.thread_id);
    const sender = transcript.match(/sender=(.*?)user:([a-z0-9-]+)/)?.[2];
    expect(sender).toBeTruthy();
    expect(transcript.split(`user:${sender}`).length).toBeGreaterThanOrEqual(3);
    expect(await state(request, form.thread_id)).toContain("First item");
    expect(await state(request, form.thread_id)).not.toContain("- Second item");
    const countSubmissions = async () =>
      (await state(request, form.thread_id)).split(
        "Form submitted: Review items",
      ).length;
    const beforeRetry = await countSubmissions();
    expect(
      (
        await request.post("/mock/slack/submit", {
          data: { view, user: "U_ALICE" },
        })
      ).ok(),
    ).toBeTruthy();
    expect(await countSubmissions()).toBe(beforeRetry);
    expect(await click(request, form)).toEqual({});
    const reopened = (await views(request))[1].view;
    const anotherView = { ...reopened, id: "V3", state: view.state };
    expect(
      (
        await request.post("/mock/slack/submit", {
          data: { view: anotherView, user: "U_ALICE" },
        })
      ).ok(),
    ).toBeTruthy();
    expect(await countSubmissions()).toBe(beforeRetry);
  });

  test("a different submitter, malformed selection, and unknown form do not dispatch", async ({
    request,
  }) => {
    const form = await openForm(request);
    expect((await click(request, form, "U_BOB")).status).toBe("ignored");
    expect(await views(request)).toHaveLength(0);
    expect(await click(request, form)).toEqual({});
    const original = (await views(request))[0].view;
    const before = await state(request, form.thread_id);
    const view: FormView = {
      ...original,
      id: "V2",
      state: {
        values: {
          choices: { selected: { selected_options: [{ value: "999" }] } },
        },
      },
    };
    for (const submitted of [
      { view, user: "U_BOB" },
      { view, user: "U_ALICE" },
      {
        view: {
          ...view,
          private_metadata: JSON.stringify({
            channel_id: "C_DEMO",
            form_id: "missing",
          }),
        },
        user: "U_ALICE",
      },
    ]) {
      const response = await request.post("/mock/slack/submit", {
        data: submitted,
      });
      expect(await response.json()).toEqual({});
    }
    expect(await state(request, form.thread_id)).toBe(before);
    const stale = {
      ...form,
      action: {
        ...form.action,
        value: JSON.stringify({
          type: "open_swe_form",
          fingerprint: "missing",
        }),
      },
    };
    expect((await click(request, stale)).status).toBe("ignored");
    expect(await views(request)).toHaveLength(1);

    const mapped = await request.get(
      `/store/items?namespace=slack_thread_map.C_DEMO&key=${encodeURIComponent(form.thread_ts)}`,
    );
    const mapping = (await mapped.json()) as {
      value: { thread_id: string; channel_id: string; thread_ts: string };
    };
    expect(mapping.value.thread_id).toBe(form.thread_id);
    const remapped = await request.put("/store/items", {
      data: {
        namespace: ["slack_thread_map", "C_DEMO"],
        key: form.thread_ts,
        value: {
          ...mapping.value,
          thread_id: "00000000-0000-0000-0000-000000000001",
        },
      },
    });
    expect(remapped.ok()).toBeTruthy();
    expect((await click(request, form)).status).toBe("ignored");
    expect(await views(request)).toHaveLength(1);
    const response = await request.post("/mock/slack/submit", {
      data: {
        view: {
          ...view,
          state: {
            values: {
              choices: { selected: { selected_options: [{ value: "0" }] } },
            },
          },
        },
        user: "U_ALICE",
      },
    });
    expect(await response.json()).toEqual({});
    expect(await state(request, form.thread_id)).toBe(before);
  });
});
