// Lists the browser tests that failed and then passed on retry, from each Playwright JSON
// report named on the command line, as warnings and in the job summary, so a flake stays
// visible without failing the run.
import { appendFileSync, existsSync, readFileSync } from "node:fs";

const flaky = [];
const visit = (suite) => {
  for (const spec of suite.specs ?? []) {
    if (spec.tests.some((test) => test.status === "flaky")) {
      flaky.push(`${spec.file}:${spec.line} › ${spec.title}`);
    }
  }
  for (const child of suite.suites ?? []) visit(child);
};
for (const reportPath of process.argv
  .slice(2)
  .filter((path) => existsSync(path))) {
  for (const suite of JSON.parse(readFileSync(reportPath, "utf8")).suites)
    visit(suite);
}

for (const test of flaky)
  console.log(`::warning title=Flaky browser test::${test}`);
const summary = process.env.GITHUB_STEP_SUMMARY;
if (flaky.length > 0 && summary) {
  const lines = flaky.map((test) => `- \`${test}\``).join("\n");
  appendFileSync(
    summary,
    `### Flaky browser tests\n\nFailed, then passed on retry:\n\n${lines}\n`,
  );
}
