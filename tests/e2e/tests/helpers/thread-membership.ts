import { expect, type APIRequestContext } from "@playwright/test";

export async function admitThreadParticipant(
  owner: APIRequestContext,
  applicant: APIRequestContext,
  threadId: string,
  login: string,
) {
  const headers = {
    origin: `http://127.0.0.1:${process.env.E2E_UI_PORT ?? 3100}`,
  };
  const requested = await applicant.post(
    `/dashboard/api/threads/${threadId}/membership`,
    { headers },
  );
  expect(requested.ok(), await requested.text()).toBeTruthy();
  const approved = await owner.post(
    `/dashboard/api/threads/${threadId}/membership/${login}`,
    {
      headers,
      data: { approve: true },
    },
  );
  expect(approved.ok(), await approved.text()).toBeTruthy();
}
