import { expect, test } from "@playwright/test";
import {
  loginAs,
  SAME_USER,
  OTHER_USER,
  SAME_ORIGIN_HEADERS,
  seedOpenPullRequest,
} from "./helpers/dashboard";

interface Review {
  id: number;
  state: string;
  body: string;
  author: string;
}

interface Pull {
  number: number;
  head_sha: string;
  reviews: Review[];
  review_decision: string;
}

for (const description of ["Only updates documentation.", ""]) {
  test(`description reviewer blocks ${description ? "a false claim" : "an important omission"} and clears it after a body-only edit`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(180_000);
    await page.request.post("/control/reset");
    await loginAs(page, OTHER_USER);
    const existingProfile = await (
      await page.request.get("/dashboard/api/profile")
    ).json();
    await page.request.put("/dashboard/api/profile", {
      headers: SAME_ORIGIN_HEADERS,
      data: { ...existingProfile, experimental_description_review: false },
    });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/my-settings/experiments");
    const experiment = page.getByRole("switch", {
      name: "Block until PR descriptions are verified",
    });
    if (!(await experiment.isChecked())) await experiment.click();
    await expect(experiment).toBeChecked();
    await expect
      .poll(
        async () =>
          (await (await page.request.get("/dashboard/api/profile")).json())
            .experimental_description_review,
      )
      .toBe(true);
    await page.screenshot({
      path: testInfo.outputPath("description-review-experiment.png"),
      fullPage: true,
    });
    await loginAs(page, SAME_USER);
    const pr = await seedOpenPullRequest(page, {
      repo: "fakeorg/demo",
      author: "bob",
      title: "Add request timeout",
      body: description,
      head: "description-review-e2e",
      files: { "settings.py": "TIMEOUT_SECONDS = 60\n" },
    });
    const pull = async (): Promise<Pull> => {
      const response = await page.request.get("/mock/github/data");
      expect(response.ok()).toBeTruthy();
      const pulls = (await response.json()) as Pull[];
      return pulls.find((item) => item.number === pr.number)!;
    };
    const start = await page.request.post("/control/description-review", {
      data: { repo: "fakeorg/demo", number: pr.number },
    });
    expect(start.ok(), await start.text()).toBeTruthy();
    await expect
      .poll(
        async () =>
          (await pull()).reviews.some(
            (review) =>
              review.state === "CHANGES_REQUESTED" &&
              review.body.includes("settings.py:1"),
          ),
        { timeout: 90_000 },
      )
      .toBe(true);
    const blocked = await pull();
    const review = blocked.reviews.find(
      (item) =>
        item.state === "CHANGES_REQUESTED" &&
        item.body.includes("settings.py:1"),
    )!;
    expect(blocked.reviews[0].state).toBe("CHANGES_REQUESTED");
    expect(blocked.reviews[0].body).toContain("verification pending");
    expect(review.author).toBe("open-swe[bot]");
    expect(review.body).toContain("settings.py:1");
    expect(review.body).toContain("60-second timeout");
    expect(blocked.review_decision).toBe("CHANGES_REQUESTED");
    const merge = await page.request.put(
      `/fake-gh/repos/fakeorg/demo/pulls/${pr.number}/merge`,
      {
        data: { sha: blocked.head_sha, merge_method: "squash" },
      },
    );
    expect(merge.status()).toBe(405);

    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto(`/agents/reviews/fakeorg/demo/${pr.number}`);
    await expect(
      page.getByText("Changes requested.", { exact: true }).first(),
    ).toBeVisible();
    await page.getByRole("tab", { name: /Discussion/ }).click();
    await expect(
      page
        .getByRole("region", { name: "Conversation" })
        .getByText("Changes requested — PR description", { exact: false }),
    ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("description-review-blocked.png"),
      fullPage: true,
    });

    const edit = await page.request.post("/control/description-review", {
      data: {
        repo: "fakeorg/demo",
        number: pr.number,
        body: "Adds a request timeout of 60 seconds.",
      },
    });
    expect(edit.ok(), await edit.text()).toBeTruthy();
    await expect
      .poll(
        async () =>
          (await pull()).reviews.find((item) => item.id === review.id)?.state,
        { timeout: 90_000 },
      )
      .toBe("DISMISSED");
    const corrected = await pull();
    expect(corrected.head_sha).toBe(blocked.head_sha);
    expect(corrected.reviews.some((item) => item.state === "APPROVED")).toBe(
      false,
    );
    expect(corrected.review_decision).not.toBe("CHANGES_REQUESTED");
    expect(
      corrected.reviews.filter((item) => item.state === "CHANGES_REQUESTED"),
    ).toHaveLength(0);
    expect(
      corrected.reviews.filter((item) => item.state === "COMMENTED"),
    ).toHaveLength(1);
    await page.reload();
    await expect(
      page.getByText("Adds a request timeout of 60 seconds.", { exact: true }),
    ).toBeVisible();
    await page.getByRole("tab", { name: /Discussion/ }).click();
    await expect(
      page
        .getByRole("region", { name: "Conversation" })
        .getByText(
          "Open SWE reviewed this PR and found no potential bugs to report.",
          { exact: false },
        ),
    ).toBeVisible();
    await expect(
      page.getByText("Changes requested.", { exact: true }).first(),
    ).toBeHidden();
    await page.screenshot({
      path: testInfo.outputPath("description-review-corrected.png"),
      fullPage: true,
    });
  });
}

test("description experiment applies to the PR author, not the requester", async ({
  page,
}) => {
  await page.request.post("/control/reset");
  await seedOpenPullRequest(page, { title: "Unrelated first PR" });
  await seedOpenPullRequest(page, { title: "Unrelated second PR" });
  await loginAs(page, OTHER_USER);
  let profile = await (await page.request.get("/dashboard/api/profile")).json();
  await page.request.put("/dashboard/api/profile", {
    headers: SAME_ORIGIN_HEADERS,
    data: { ...profile, experimental_description_review: false },
  });
  await loginAs(page, SAME_USER);
  profile = await (await page.request.get("/dashboard/api/profile")).json();
  await page.request.put("/dashboard/api/profile", {
    headers: SAME_ORIGIN_HEADERS,
    data: { ...profile, experimental_description_review: true },
  });
  const pr = await seedOpenPullRequest(page, {
    repo: "fakeorg/demo",
    author: "bob",
    title: "Add request timeout",
    body: "Only documentation.",
    head: "description-review-disabled",
    files: { "settings.py": "TIMEOUT_SECONDS = 60\n" },
  });
  const start = await page.request.post("/control/description-review", {
    data: { repo: "fakeorg/demo", number: pr.number },
  });
  expect(start.ok()).toBeTruthy();
  await expect
    .poll(
      async () => {
        const pulls = (await (
          await page.request.get("/mock/github/data")
        ).json()) as Pull[];
        return pulls
          .find((p) => p.number === pr.number)
          ?.reviews.some((review) => review.state === "COMMENTED");
      },
      { timeout: 90_000 },
    )
    .toBe(true);
  const pulls = (await (
    await page.request.get("/mock/github/data")
  ).json()) as Pull[];
  expect(
    pulls
      .find((p) => p.number === pr.number)
      ?.reviews.some((review) => review.state === "CHANGES_REQUESTED"),
  ).toBe(false);
});
