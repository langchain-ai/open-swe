# Enterprise-Grade AI Coding Agent (Open-SWE Enterprise) - Project Plan & Architecture Design

This document details the blueprint for transforming the base Open-SWE framework into an enterprise-ready, production-grade AI coding agent platform. By combining safety, multi-agent collaboration, and scientific evaluation, this project demonstrates high engineering maturity and directly addresses real-world corporate adoption barriers.

---

## 1. Project Vision

Three core pillars of enterprise AI engineering:
1. **Safety & Compliance (Sandbox Security Gateway)**: Enforces command-level and file-level security policies with human-in-the-loop (HITL) approval, making it safe to run in corporate networks.
2. **Architecture Depth (LangGraph Multi-Agent Collaboration)**: Divides complex software tasks among specialized roles (PM, Architect, Coding, QA), reducing token usage and error accumulation.
3. **Scientific Evaluation (Private SWE-bench CI-Eval)**: Measures agent performance quantitatively across real-world historical bug PRs, introducing CI/CD metrics to AI engineering.

```
       +--------------------------------------------------------+
       |             Private SWE-bench Eval Pipeline            |
       |  (Parallel Sandboxes + Pass@1 + Cost & AST Metrics)   |
       +--------------------------------------------------------+
                                   | Evaluates
                                   v
       +--------------------------------------------------------+
       |             LangGraph Multi-Agent Orchestration        |
       |       [PM Node] -> [Architect Node] -> [Coding Node]   |
       |                                           ^            |
       |                                           | Refines    |
       |                                           v            |
       |                                       [QA Node]        |
       +--------------------------------------------------------+
                                   | Executes commands via
                                   v
       +--------------------------------------------------------+
       |             Sandbox Security Firewall (Gateway)        |
       |  (shlex Parsing + Command Blacklist + Slack Callback)  |
       +--------------------------------------------------------+
```

---

## 2. Phase 1: Sandbox Security Gateway (Enterprise Shield)

### Core Goals
- Intercept all shell commands before execution in Daytona, Modal, or LangSmith sandboxes.
- Detect and prevent high-risk actions (e.g., `rm -rf`, raw token exposure in `curl`, system file modifications).
- Implement Human-in-the-Loop (HITL) interactive approval via Slack block kit or Local REST API mockup.

### Detailed Technical Decisions (Agreed via Interview)
- **Debugging Sandbox**: Local / Docker-based environment (`create_local_sandbox` in `agent/integrations/local.py`).
- **Safety Interceptor**: Wrap the sandbox backend inside `AuditingSandboxWrapper`.
- **Command Risk Classifier (Hybrid Engine)**:
  - **Level 1 (Static Regex/Keywords)**: Rapidly block absolute threats (like `rm -rf /`, `mkfs`, etc.).
  - **Level 2 (Inference-based LLM Classification)**: For ambiguous/complex commands (like heavy shell loops, curl requests, or custom scripts), invoke a lightweight LLM using structured outputs to assess threat levels (Low, Medium, High).
- **Audit & Approval Persistence (SQLite Store)**:
  - SQLite database at `agent_safety.db` inside the workspace containing:
    - `audit_trail` table: records all executed command strings, execution timestamps, duration, risk levels, and exit codes.
    - `approvals` table: records pending and resolved interactive human approvals.
- **HITL Block Flow (Blocking & Polling)**:
  - When a `Medium Risk` command is detected, the wrapper inserts it into the `approvals` table with status `PENDING`.
  - It prints a prominent notification and enters a synchronous loop, polling the database every 1 second (up to a 5-minute timeout) waiting for the status to become `APPROVED` or `REJECTED`.
- **Mock Callback Flow (REST API & CLI Script)**:
  - A FastAPI GET endpoint `/safety/approvals` in `agent/webapp.py` returns pending approvals.
  - A FastAPI POST endpoint `/safety/approve` accepts an approval or rejection and updates the SQLite database.
  - A helper CLI script `scripts/approve_cmd.py` lets developers view and approve/reject commands from their terminal.

### Files Involved
- [NEW] `agent/utils/sandbox_safety.py` (AuditingSandboxWrapper, Policy Engine, SQLite DB schemas, and blocking logic)
- [MODIFY] `agent/server.py` (Wrap the sandbox backend with the safety proxy)
- [MODIFY] `agent/webapp.py` (FastAPI GET and POST endpoints for safety mock and Slack Webhooks)
- [NEW] `scripts/approve_cmd.py` (CLI interactive approval utility)

---

## 3. Phase 2: LangGraph Multi-Agent Orchestration (Architecture Depth)

### Core Goals
- Break down single-agent complexity into a highly focused team of expert nodes.
- **PM Node**: Analyzes issues/tasks, generates a structured test plan, and populates `test_plan` in state.
- **Architect Node**: Performs static code analysis using AST parsing and dependency tracking in the sandbox, identifying target files and functions to minimize context window size.
- **Coding Node**: Focuses exclusively on writing code inside the sandbox using targeted file scopes.
- **QA Node**: Detects modified files, runs only relevant test files, intercepts test logs, and manages self-reflection loops.

### State & Topology Design
- Define a unified `MultiAgentState` to pass lightweight metadata (paths, diffs, test logs) instead of bloated files.
- Orchestrate the nodes in `agent/server.py` using LangGraph `StateGraph`.

### Files Involved
- [NEW] `agent/multi_agent/state.py` (State schema)
- [NEW] `agent/multi_agent/nodes.py` (PM, Architect, and QA nodes)
- [MODIFY] `agent/server.py` (Reconfigure the state graph to connect all nodes)

---

## 4. Phase 3: Private SWE-bench CI-Eval (Quantitative Metric)

### Core Goals
- Establish an automated, repeatable evaluation pipeline to benchmark Pass@1 rates and token costs.
- Automatically harvest historical bug fixes from the team's repositories.
- Run tests concurrently across independent sandboxes.

### Component Design
- **Dataset Generator**: Scans git history for PRs tagged as bug fixes, extracts the problem description, `before_sha` (buggy state), `patch` (golden patch), and the specific test command.
- **Concurrently Runner**: Spawns parallel sandboxes using `asyncio` to run the agent against 30+ test cases.
- **Evaluator**: Runs unit tests after the agent finishes. Calculates:
  - **Pass@1**: Percentage of cases successfully passing the unit test.
  - **Cost Index**: Token consumption and inference cost per run.
  - **Edit Similarity**: AST structural differences comparing agent changes to the human golden patch.

### Files Involved
- [NEW] `evals/swe/build_dataset.py` (Crawls git and exports `swe_benchmark.jsonl`)
- [NEW] `evals/swe/run_eval.py` (Runner executing parallel eval sandboxes)
- [NEW] `evals/swe/config.toml` (Eval and judge settings)

---

## 6. Next Steps & Development Checklist

1. [ ] **Sandbox Safety Proxy (Phase 1, Milestone 1)**:
   - Create SQLite db setup and safety helper functions in `agent/utils/sandbox_safety.py`.
   - Write keyword list and basic LLM classifier function (using `make_model` or generic client).
   - Implement `AuditingSandboxWrapper.execute` matching `SandboxBackendProtocol`.
2. [ ] **FastAPI Endpoints (Phase 1, Milestone 2)**:
   - Implement `/safety/approvals` and `/safety/approve` in `agent/webapp.py`.
   - Implement `/safety/approvals/ui` to render a clean HTML dashboard using Tailwind/Vanilla CSS to display pending, approved, and blocked commands with premium dark-mode styling!
3. [ ] **CLI approve_cmd Script (Phase 1, Milestone 3)**:
   - Create `scripts/approve_cmd.py`.
4. [ ] **Integration Test**:
   - Wrap the sandbox in `agent/server.py` and run a dummy local run.
