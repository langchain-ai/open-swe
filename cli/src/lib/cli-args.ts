// Top-level CLI argument parsing for the openswe binary.
// Keep this sync and dependency-light: it runs once at startup before
// the Ink renderer takes over.

export type CliCommand =
  | 'local'
  | 'login'
  | 'logout'
  | 'whoami'
  | 'runs'
  | 'attach'
  | 'new-cloud'
  | 'new-local'
  | 'upgrade'
  | 'handoff';

export type HandoffDirection = 'local' | 'cloud';

export type ParsedArgs = {
  command: CliCommand;
  backend_url?: string;
  thread_id?: string;
  repo?: string;
  branch?: string;
  prompt?: string;
  model?: string;
  agent?: string;
  cloud?: boolean;
  version?: string;
  handoff_to?: HandoffDirection;
};

type FlagSpec = {
  long: string;
  takesValue: boolean;
};

const FLAG_SPECS: FlagSpec[] = [
  { long: 'cloud', takesValue: false },
  { long: 'local', takesValue: false },
  { long: 'repo', takesValue: true },
  { long: 'branch', takesValue: true },
  { long: 'prompt', takesValue: true },
  { long: 'model', takesValue: true },
  { long: 'agent', takesValue: true },
  { long: 'backend', takesValue: true },
  { long: 'version', takesValue: true },
  { long: 'to', takesValue: true },
  { long: 'thread', takesValue: true },
  { long: 'login', takesValue: true },
];

type ParsedFlags = {
  flags: Record<string, string | true | undefined>;
  positionals: string[];
};

const parseFlags = (argv: string[]): ParsedFlags => {
  const flags: Record<string, string | true | undefined> = {};
  const positionals: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a.startsWith('--')) {
      const eq = a.indexOf('=');
      const name = eq === -1 ? a.slice(2) : a.slice(2, eq);
      const inlineValue = eq === -1 ? undefined : a.slice(eq + 1);
      const spec = FLAG_SPECS.find((s) => s.long === name);
      if (!spec) {
        // Unknown flag — treat as a boolean toggle so we don't crash on extras.
        flags[name] = inlineValue ?? true;
        continue;
      }
      if (spec.takesValue) {
        if (inlineValue !== undefined) {
          flags[name] = inlineValue;
        } else {
          const next = argv[i + 1];
          if (next === undefined || next.startsWith('--')) {
            flags[name] = '';
          } else {
            flags[name] = next;
            i++;
          }
        }
      } else {
        flags[name] = true;
      }
    } else {
      positionals.push(a);
    }
  }
  return { flags, positionals };
};

const asString = (v: string | true | undefined): string | undefined =>
  typeof v === 'string' && v.length > 0 ? v : undefined;

export const parseArgs = (argv: string[] = process.argv.slice(2)): ParsedArgs => {
  const { flags, positionals } = parseFlags(argv);
  const backend = asString(flags.backend) ?? process.env.OPENSWE_BACKEND;
  // `openswe --login <url>` is an ergonomic alias for `openswe login <url>`.
  // Without this the URL gets swallowed as a positional and the parser falls
  // back to local mode, which silently skips the OAuth flow.
  const loginFlagUrl = asString(flags.login);
  if (loginFlagUrl !== undefined) {
    return {
      command: 'login',
      backend_url: loginFlagUrl,
      repo: asString(flags.repo),
      branch: asString(flags.branch),
      prompt: asString(flags.prompt),
      model: asString(flags.model),
      agent: asString(flags.agent),
      cloud: flags.cloud === true,
      version: asString(flags.version),
    };
  }
  const sub = positionals[0];

  const base: ParsedArgs = {
    command: 'local',
    backend_url: backend,
    repo: asString(flags.repo),
    branch: asString(flags.branch),
    prompt: asString(flags.prompt),
    model: asString(flags.model),
    agent: asString(flags.agent),
    cloud: flags.cloud === true,
    version: asString(flags.version),
  };

  if (!sub) return base;

  switch (sub) {
    case 'login':
      return { ...base, command: 'login', backend_url: positionals[1] ?? backend };
    case 'logout':
      return { ...base, command: 'logout' };
    case 'whoami':
      return { ...base, command: 'whoami' };
    case 'runs':
      return { ...base, command: 'runs' };
    case 'attach': {
      const tid = positionals[1];
      return { ...base, command: 'attach', thread_id: tid };
    }
    case 'upgrade':
      return { ...base, command: 'upgrade', version: base.version ?? positionals[1] };
    case 'handoff': {
      const toVal = asString(flags.to);
      const direction: HandoffDirection | undefined =
        toVal === 'local' || toVal === 'cloud' ? toVal : undefined;
      const threadId = asString(flags.thread) ?? positionals[1];
      return {
        ...base,
        command: 'handoff',
        handoff_to: direction,
        thread_id: threadId,
      };
    }
    case 'new': {
      const isCloud = flags.cloud === true || flags.local !== true;
      const positionalPrompt = positionals.slice(1).join(' ').trim();
      const prompt = base.prompt ?? (positionalPrompt.length > 0 ? positionalPrompt : undefined);
      return {
        ...base,
        command: isCloud ? 'new-cloud' : 'new-local',
        prompt,
      };
    }
    default:
      return base;
  }
};

// Backwards-compatible name used by initial scaffolding callers.
export const parseCliArgs = (argv: string[] = process.argv.slice(2)): ParsedArgs =>
  parseArgs(argv);
