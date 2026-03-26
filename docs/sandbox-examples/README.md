# Sandbox Integration Examples

This directory contains documentation and examples for sandbox providers in Open SWE.

## Available Documentation

- **[Docker Sandbox](./docker-sandbox.md)** - Run agents in isolated Docker containers on your local machine or Docker host. Ideal for local development, self-hosted deployments, and CI/CD pipelines.

- **[Custom Sandbox Guide](./custom-sandbox-guide.md)** - Learn how to implement your own sandbox provider when the built-in options don't meet your needs.

## Built-in Sandbox Providers

Open SWE supports multiple sandbox providers out of the box:

| Provider | Type | Description |
|----------|------|-------------|
| LangSmith | `langsmith` | Cloud sandboxes via LangSmith (default) |
| Modal | `modal` | Serverless containers via Modal |
| Daytona | `daytona` | Development environments via Daytona |
| Runloop | `runloop` | Devbox sandboxes via Runloop |
| Docker | `docker` | Local Docker containers |
| Local | `local` | Direct shell execution (no isolation) |

## Quick Start

Set the `SANDBOX_TYPE` environment variable to choose your provider:

```bash
# Use Docker locally
export SANDBOX_TYPE=docker

# Use LangSmith cloud sandboxes
export SANDBOX_TYPE=langsmith

# Use Modal
export SANDBOX_TYPE=modal
```

See the individual documentation files for detailed configuration options.