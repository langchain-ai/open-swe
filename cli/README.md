```bash
  ░██████    ░██████   ░███████      ░███    
 ░██   ░██  ░██   ░██  ░██   ░██    ░██░██   
░██        ░██     ░██ ░██    ░██  ░██  ░██  
░██        ░██     ░██ ░██    ░██ ░█████████ 
░██        ░██     ░██ ░██    ░██ ░██    ░██ 
 ░██   ░██  ░██   ░██  ░██   ░██  ░██    ░██ 
  ░██████    ░██████   ░███████   ░██    ░██ 
```
AI coding agent CLI: Interact with LLM (via OpenRouter) for filesystem ops, shell commands, and code tasks. Built with Ink (TUI), LangGraph (agent graph), TypeScript.

## Setup

1. Install Bun: `curl -fsSL https://bun.sh/install | bash`
2. Clone: `git clone <repo> && cd coda`
3. Deps: `bun install`
4. Set up LangSmith (optional, for tracing):
   - Sign up at [LangSmith](https://smith.langchain.com)
   - Create API key in Settings
   - Add to `.env`:
     ```bash
     LANGSMITH_TRACING=true
     LANGSMITH_API_KEY=your_api_key_here
     LANGSMITH_PROJECT=coda
     ```

## Dev

- Build: `bun run build`
- Watch: `bun run dev`
- Run: `bun run start`
- Test: `bun run test`
- Logs (debug): `bun run logs:tail` (tail `~/.coda/logs/coda.log` in separate terminal)


## Link
- Build: `bun run build`
- Link: `bun link`

In your working directory:
- Run: `coda`

## Usage

Run the interactive CLI with: `bun run start`.

The application will prompt for an OpenRouter API key on the first run.

**Commands**: `/help`, `/status`, `/model`, `/review`, `/reset`, `/clear`, `/quit`.
**Keys**: `Tab` to switch modes (agent/plan). `Esc` to interrupt/exit.

## Structure

- `src/coda.tsx`: Main entrypoint, Ink renderer setup.
- `src/app/`: Core application logic (agent runner, command executor, state store).
- `src/agent/`: LangGraph agent, tools (fs, shell), and prompts.
- `src/tui/`: TUI components and hooks built with Ink.
- `src/lib/`: Shared utilities (storage, logger, diff).

See `AGENTS.md` for detailed contributor guidelines.