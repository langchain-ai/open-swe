// Top-level CLI argument parsing for the openswe binary.
// Keep this sync and dependency-light: it runs once at startup before
// the Ink renderer takes over.

export type CliCommand =
  | 'menu'
  | 'login'
  | 'logout'
  | 'whoami'
  | 'runs'
  | 'attach'
  | 'new-cloud'
  | 'upgrade';

export type ParsedArgs = {
  command: CliCommand;
  backend_url?: string;
  thread_id?: string;
  repo?: string;
  branch?: string;
  prompt?: string;
  model?: string;
  agent?: string;
  version?: string;
};

type FlagSpec = {
  long: string;
  takesValue: boolean;
};

const FLAG_SPECS: FlagSpec[] = [
  { long: 'repo', takesValue: true },
  { long: 'branch', takesValue: true },
  { long: 'prompt', takesValue: true },
  { long: 'model', takesValue: true },
  { long: 'agent', takesValue: true },
  { long: 'backend', takesValue: true },
  { long: 'version', takesValue: true },
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
      version: asString(flags.version),
    };
  }
  const sub = positionals[0];

  const base: ParsedArgs = {
    command: 'menu',
    backend_url: backend,
    repo: asString(flags.repo),
    branch: asString(flags.branch),
    prompt: asString(flags.prompt),
    model: asString(flags.model),
    agent: asString(flags.agent),
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
    case 'new': {
      const positionalPrompt = positionals.slice(1).join(' ').trim();
      const prompt = base.prompt ?? (positionalPrompt.length > 0 ? positionalPrompt : undefined);
      return { ...base, command: 'new-cloud', prompt };
    }
    default:
      return base;
  }
};

// Backwards-compatible name used by initial scaffolding callers.
export const parseCliArgs = (argv: string[] = process.argv.slice(2)): ParsedArgs =>
  parseArgs(argv);
