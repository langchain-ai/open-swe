export const GITHUB_TRIGGER_USERNAME = process.env.GITHUB_TRIGGER_USERNAME
  ? `@${process.env.GITHUB_TRIGGER_USERNAME}`
  : "@open-swe";
