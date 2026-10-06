/*
 * RFC 9457 problem details, standing in for the app's generated API client.
 *
 * The app's version allowlists three GTM problem types as person-facing. A
 * package cannot know a consumer's type URLs, so the rule generalizes to the
 * status: a 4xx states something the caller did and its `detail` is worth
 * showing, a 5xx states something broke and is never shown. The fallback the
 * call site passes always wins over a machine refusal.
 */

interface Problem {
  type: string;
  title?: string;
  status?: number;
  detail?: string;
  instance?: string;
}

export class ProblemError extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.title ?? problem.type);
    this.name = "ProblemError";
    this.problem = problem;
  }
}

const HTTP_STATUS_LINE = /^\s*\d{3}\b/;

function personFacing(problem: Problem): boolean {
  const status = problem.status;
  return status !== undefined && status >= 400 && status < 500;
}

export function problemMessage(error: unknown, fallback: string): string {
  if (error instanceof ProblemError) {
    const detail = error.problem.detail;
    if (personFacing(error.problem) && detail !== undefined && detail.trim().length > 0) {
      return detail;
    }
    return fallback;
  }
  if (
    error instanceof Error &&
    error.message.trim().length > 0 &&
    !HTTP_STATUS_LINE.test(error.message)
  ) {
    return error.message;
  }
  return fallback;
}

export function isProblemOfType(error: unknown, type: string): boolean {
  return error instanceof ProblemError && error.problem.type === type;
}

export type { Problem };
