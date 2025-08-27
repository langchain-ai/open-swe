# Open-SWE CLI

LangChain's official CLI for Open-SWE Web - an AI-powered coding assistant.

## Installation

```bash
npm install -g openswe-cli
```

## Usage

### Basic Commands

```bash
# Start interactive chat mode
openswe

# Start with a specific task
openswe "fix the bug in src/app.js"

# Start server mode
openswe --server

# Show help
openswe --help

# Show version
openswe --version
```

### Command Options

- `--server` - Start in server mode for web interface integration
- `--help` - Show help information
- `--version` - Show version information

## Examples

```bash
# Interactive mode
openswe

# Direct task
openswe "add unit tests to the auth module"

# Server mode
openswe --server
```

## What it can do

Open-SWE can help with:

- Reading and editing files
- Running builds and tests
- Code analysis and debugging
- Adding new features
- Project exploration

Simply describe what you want in natural language and it will help you get it done.
