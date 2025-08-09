# SWE-bench Evaluation for Open SWE Agent

This directory contains the SWE-bench evaluation system for the Open SWE agent. [SWE-bench](https://www.swebench.com/) is a benchmark for evaluating large language models on real-world software engineering tasks.

## Overview

The SWE-bench evaluation system allows you to:

- Evaluate the Open SWE agent's ability to fix real GitHub issues
- Generate patches from the agent's branch changes
- Run the official SWE-bench harness to verify fixes
- Get detailed metrics on test outcomes (fail-to-pass, pass-to-pass, etc.)

## Prerequisites

### System Requirements

- **Python 3.8+**: Required for running the SWE-bench harness
- **Docker**: Required for isolated test execution
  - Minimum 16GB RAM recommended
  - ~120GB disk space for Docker images
- **Node.js 18+**: For running the TypeScript evaluation code
- **Git**: For repository operations

### Environment Setup

1. **Install Python dependencies**:

   ```bash
   # The setup script will handle this, or manually:
   pip install swebench
   ```

2. **Verify Docker installation**:

   ```bash
   docker --version
   docker run hello-world
   ```

3. **Set GitHub authentication**:
   ```bash
   export GITHUB_PAT="your-github-personal-access-token"
   ```

## Quick Start

### 1. Run Setup Script

```bash
# From the apps/open-swe directory
yarn setup:swe-bench

# Or with options:
yarn setup:swe-bench --download-dataset --dataset=princeton-nlp/SWE-bench_Lite
```

Setup script options:

- `--skip-python-install`: Skip installing the swebench Python package
- `--skip-docker-check`: Skip Docker verification
- `--download-dataset`: Download the SWE-bench dataset
- `--dataset=NAME`: Specify dataset (default: princeton-nlp/SWE-bench_Lite)
- `--no-create-dirs`: Skip creating directories

### 2. Run Evaluations

```bash
# Run SWE-bench evaluations
yarn eval:swe-bench

# With environment variables:
SWE_BENCH_INSTANCE_IDS="sympy__sympy-20590,django__django-13658" yarn eval:swe-bench
```

## Configuration

### Environment Variables

| Variable                    | Description                                 | Default                        |
| --------------------------- | ------------------------------------------- | ------------------------------ |
| `GITHUB_PAT`                | GitHub Personal Access Token for API access | Required                       |
| `SWE_BENCH_DATASET_NAME`    | Dataset to evaluate against                 | `princeton-nlp/SWE-bench_Lite` |
| `SWE_BENCH_INSTANCE_IDS`    | Comma-separated list of specific instances  | All instances                  |
| `SWE_BENCH_MAX_WORKERS`     | Number of parallel Docker workers           | `1`                            |
| `SWE_BENCH_TIMEOUT_MINUTES` | Timeout per instance in minutes             | `120`                          |
| `SWE_BENCH_CACHE_LEVEL`     | Docker cache level (none/base/env/instance) | `env`                          |
| `SWE_BENCH_CLEANUP`         | Clean up artifacts after evaluation         | `true`                         |

### Example Configuration

```bash
# Evaluate specific instances with more workers
export SWE_BENCH_INSTANCE_IDS="sympy__sympy-20590,django__django-13658"
export SWE_BENCH_MAX_WORKERS=4
export SWE_BENCH_TIMEOUT_MINUTES=60
yarn eval:swe-bench

# Run full SWE-bench dataset
export SWE_BENCH_DATASET_NAME="princeton-nlp/SWE-bench"
export SWE_BENCH_MAX_WORKERS=8
yarn eval:swe-bench
```

## How It Works

1. **Agent Execution**: The evaluation runs the Open SWE agent on each SWE-bench instance
2. **Branch Creation**: The agent creates a branch with proposed fixes
3. **Patch Generation**: The system generates a git diff from the agent's branch
4. **SWE-bench Harness**: The Python harness applies the patch and runs tests in Docker
5. **Results Collection**: Test outcomes are parsed and converted to evaluation metrics

## Output and Results

### Evaluation Metrics

Each instance evaluation returns:

- `resolved`: Whether the issue was successfully fixed (0 or 1)
- `patch_applied`: Whether the patch applied cleanly
- `fail_to_pass`: Ratio of failing tests that now pass
- `pass_to_pass`: Ratio of passing tests that still pass
- `test_regressions`: Penalty for any new test failures
- `modified_files`: Number of files changed
- `evaluation_duration`: Time taken in seconds

### Result Files

Results are stored in:

- `./swe-bench-evals/predictions/`: JSONL prediction files
- `./evaluation_results/`: Detailed evaluation reports
- `./logs/run_evaluation/`: Execution logs

## Troubleshooting

### Common Issues

1. **Docker not running**:

   ```bash
   # Start Docker daemon
   sudo systemctl start docker  # Linux
   # Or use Docker Desktop on macOS/Windows
   ```

2. **Insufficient Docker resources**:

   ```bash
   # Check Docker memory
   docker info | grep Memory

   # Increase Docker memory in Docker Desktop settings
   ```

3. **Python/pip not found**:

   ```bash
   # Install Python 3.8+
   # Ubuntu/Debian:
   sudo apt-get install python3 python3-pip

   # macOS:
   brew install python@3.8
   ```

4. **SWE-bench package issues**:

   ```bash
   # Reinstall with specific version
   pip install --upgrade swebench

   # Or from source
   pip install git+https://github.com/princeton-nlp/SWE-bench.git
   ```

5. **Patch generation failures**:
   - Ensure the agent created a valid branch
   - Check that changes don't include test files
   - Verify GitHub token has repository access

### Debug Mode

Enable detailed logging:

```bash
# Set log level
export LOG_LEVEL=DEBUG
yarn eval:swe-bench
```

Check logs in:

- `./logs/run_evaluation/<run_id>/`: Harness execution logs
- Agent execution traces in LangSmith (if configured)

### Docker Cleanup

After evaluations, clean up Docker resources:

```bash
# Remove SWE-bench containers
docker ps -a | grep swebench | awk '{print $1}' | xargs docker rm

# Remove unused images
docker image prune -a

# Full cleanup (careful!)
docker system prune -a --volumes
```

## Advanced Usage

### Custom Dataset Evaluation

To evaluate on custom instances:

1. Create a custom dataset file with SWE-bench format
2. Modify `swe-bench.eval.ts` to load your dataset
3. Run evaluation as normal

### Batch Evaluation

For large-scale evaluations:

```bash
# Increase workers and timeout
export SWE_BENCH_MAX_WORKERS=16
export SWE_BENCH_TIMEOUT_MINUTES=180

# Disable cleanup for debugging
export SWE_BENCH_CLEANUP=false

yarn eval:swe-bench
```

### Integration with CI/CD

Example GitHub Actions workflow:

```yaml
- name: Setup SWE-bench
  run: yarn setup:swe-bench --skip-docker-check

- name: Run SWE-bench Eval
  env:
    GITHUB_PAT: ${{ secrets.GITHUB_PAT }}
    SWE_BENCH_MAX_WORKERS: 4
  run: yarn eval:swe-bench
```

## Development

### File Structure

```
apps/open-swe/evals/swe-bench/
├── types.ts           # TypeScript interfaces
├── patch-generator.ts # Git diff generation
├── harness.ts        # Python harness wrapper
├── evaluator.ts      # Main evaluation logic
├── swe-bench.eval.ts # Vitest evaluation file
└── README.md         # This file
```

### Adding New Features

1. **New metrics**: Update `types.ts` and `evaluator.ts`
2. **Custom datasets**: Modify `swe-bench.eval.ts`
3. **Harness options**: Update `harness.ts` and `types.ts`

### Testing

```bash
# Test patch generation
yarn test apps/open-swe/evals/swe-bench/patch-generator.test.ts

# Test harness interface
yarn test apps/open-swe/evals/swe-bench/harness.test.ts
```

## Resources

- [SWE-bench Documentation](https://www.swebench.com/)
- [SWE-bench Paper](https://arxiv.org/abs/2310.06770)
- [Open SWE Agent Documentation](../../README.md)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)

## License

This evaluation system is part of the Open SWE project and follows the same license terms.
