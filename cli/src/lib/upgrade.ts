// Runs the curl-able installer for the openswe CLI in-process via Bun.spawn.
// Equivalent to:
//   curl -fsSL https://raw.githubusercontent.com/<repo>/<branch>/cli/scripts/install.sh \
//     | OPENSWE_VERSION=<version> sh

const DEFAULT_REPO = 'langchain-ai/open-swe';
const INSTALL_SCRIPT_BRANCH = 'main';

const scriptUrl = (repo: string): string =>
  `https://raw.githubusercontent.com/${repo}/${INSTALL_SCRIPT_BRANCH}/cli/scripts/install.sh`;

declare const Bun:
  | {
      spawn: (opts: {
        cmd: string[];
        stdout?: 'inherit' | 'pipe';
        stderr?: 'inherit' | 'pipe';
        stdin?: 'inherit' | 'pipe' | ReadableStream<Uint8Array> | null;
        env?: Record<string, string>;
      }) => {
        stdout: ReadableStream<Uint8Array> | null;
        stderr: ReadableStream<Uint8Array> | null;
        stdin: WritableStream<Uint8Array> | null;
        exited: Promise<number>;
      };
    }
  | undefined;

export type UpgradeOptions = {
  version?: string;
  repo?: string;
};

export type UpgradeResult = {
  exitCode: number;
  resolvedVersion: string;
  resolvedRepo: string;
};

// Streams output directly to the terminal. Returns the install script's exit
// code. Does NOT call process.exit — caller decides what to do.
export const runUpgrade = async (opts: UpgradeOptions = {}): Promise<UpgradeResult> => {
  const repo = opts.repo ?? process.env.OPENSWE_REPO ?? DEFAULT_REPO;
  const version = opts.version ?? 'latest';

  if (typeof Bun === 'undefined') {
    throw new Error(
      'openswe upgrade requires the bun-compiled binary (Bun.spawn unavailable in this runtime).',
    );
  }

  const curl = Bun.spawn({
    cmd: ['curl', '-fsSL', scriptUrl(repo)],
    stdout: 'pipe',
    stderr: 'inherit',
  });

  const sh = Bun.spawn({
    cmd: ['sh'],
    stdin: curl.stdout ?? undefined,
    stdout: 'inherit',
    stderr: 'inherit',
    env: {
      ...process.env,
      OPENSWE_REPO: repo,
      OPENSWE_VERSION: version,
    } as Record<string, string>,
  });

  const [curlCode, shCode] = await Promise.all([curl.exited, sh.exited]);
  const exitCode = shCode !== 0 ? shCode : curlCode;

  return { exitCode, resolvedRepo: repo, resolvedVersion: version };
};
