import { expect, test } from "@playwright/test";
import { loginAs, SAME_USER, seedOpenPullRequest } from "./helpers/dashboard";

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
            (review) => review.state === "CHANGES_REQUESTED",
          ),
        { timeout: 90_000 },
      )
      .toBe(true);
    const blocked = await pull();
    const review = blocked.reviews.find(
      (item) => item.state === "CHANGES_REQUESTED",
    )!;
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
