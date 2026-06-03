# 企业级 AI 编码智能体 (Open-SWE Enterprise) - 项目计划与架构设计

本文档详细阐述了将基础 Open-SWE 框架演进为企业级、生产级 AI 编码智能体平台的蓝图。通过结合安全性、多智能体协同以及科学的评估体系，本项目展示了极高的工程成熟度，并直接解决了企业落地应用中的核心阻碍。

---

## 1. 项目愿景

企业级 AI 工程化的三大核心支柱：
1. **安全与合规 (沙箱安全网关)**：通过人工环路 (HITL) 审批机制，强制执行命令级和文件级的安全策略，确保其在企业内网中安全运行。
2. **架构深度 (LangGraph 多智能体协同)**：将复杂的软件任务分解给专门的角色（产品经理 PM、架构师 Architect、编码人员 Coding、测试 QA），从而减少 Token 消耗并降低误差累积。
3. **科学的评估体系 (私有 SWE-bench CI-Eval)**：通过企业内部真实的历史 Bug PR 对智能体性能进行定量评估，将 CI/CD 度量指标引入 AI 工程化领域。

```
        +--------------------------------------------------------+
        |             私有 SWE-bench 评估流水线                   |
        |     (并行沙箱 + Pass@1 + 成本与 AST 结构度量)            |
        +--------------------------------------------------------+
                                    | 评估
                                    v
        +--------------------------------------------------------+
        |             LangGraph 多智能体编排                     |
        |       [PM 节点] -> [架构师节点] -> [编码节点]          |
        |                                           ^            |
        |                                           | 优化/微调  |
        |                                           v            |
        |                                       [QA 节点]        |
        +--------------------------------------------------------+
                                    | 通过以下网关执行命令
                                    v
        +--------------------------------------------------------+
        |             沙箱安全防火墙 (安全网关)                   |
        |    (shlex 解析 + 命令黑名单 + Slack 审批回调)           |
        +--------------------------------------------------------+
```

---

## 2. 阶段 1：沙箱安全网关 (企业级防御盾)

### 核心目标
- 在 Daytona、Modal 或 LangSmith 沙箱中执行所有 Shell 命令前进行拦截。
- 检测并阻止高风险操作（例如：`rm -rf`、`curl` 中直接暴露的原始 Token、修改系统敏感文件等）。
- 通过 Slack Block Kit 或本地 REST API 模拟实现人工环路 (HITL) 交互式审批。

### 详细技术决策
- **调试沙箱**：本地 / 基于 Docker 的环境（`agent/integrations/local.py` 中的 `create_local_sandbox`）。
- **安全拦截器**：将沙箱后端封装在 `AuditingSandboxWrapper` 中。
- **命令风险分类器 (混合引擎)**：
  - **级别 1 (静态正则/关键字匹配)**：快速拦截绝对的高危威胁（如 `rm -rf /`、`mkfs` 等）。
  - **级别 2 (基于推理的 LLM 分类)**：针对模糊/复杂的命令（如复杂的 shell 循环、curl 请求或自定义脚本），调用轻量级 LLM，使用结构化输出评估风险等级（低 Low、中 Medium、高 High）。
- **审计与审批持久化 (SQLite 存储)**：
  - 在工作区内的 `agent_safety.db` 中维护一个 SQLite 数据库，包含：
    - `audit_trail` 表：记录所有执行的命令字符串、执行时间戳、耗时、风险等级和退出码。
    - `approvals` 表：记录待审批和已解决的交互式人工审批任务。
- **HITL 阻塞流程 (阻塞与轮询)**：
  - 当检测到 `中度风险 (Medium Risk)` 命令时，包装器会将其插入 `approvals` 表中，状态设为 `PENDING`。
  - 它会打印显眼的通知并进入同步循环，每 1 秒轮询一次数据库（最多超时 5 分钟），等待状态变为 `APPROVED` 或 `REJECTED`。
- **模拟回调流程 (REST API & CLI 脚本)**：
  - 在 `agent/webapp.py` 中提供一个 FastAPI GET 接口 `/safety/approvals`，用于返回待审批的列表。
  - 提供一个 FastAPI POST 接口 `/safety/approve`，用于接受审批或驳回结果，并更新 SQLite 数据库。
  - 提供一个辅助 CLI 脚本 `scripts/approve_cmd.py`，允许开发人员在终端直接查看并批准/拒绝命令。

### 涉及文件
- [NEW] `agent/utils/sandbox_safety.py` (AuditingSandboxWrapper、策略引擎、SQLite 数据库表结构和阻塞逻辑)
- [MODIFY] `agent/server.py` (用安全代理包装沙箱后端)
- [MODIFY] `agent/webapp.py` (用于安全模拟和 Slack Webhook 的 FastAPI GET 和 POST 接口)
- [NEW] `scripts/approve_cmd.py` (CLI 交互式审批实用工具)

---

## 3. 阶段 2：LangGraph 多智能体编排 (架构深度)

### 核心目标
- 将单一智能体（Single-Agent）的复杂性分解为专注于特定领域的专家节点团队。
- **PM 节点**：分析 Issue/任务，生成结构化的测试计划，并填充状态中的 `test_plan`。
- **架构师节点**：在沙箱中使用 AST 解析和依赖跟踪进行静态代码分析，识别目标文件和函数，以最大限度地缩减上下文窗口（Context Window）大小。
- **编码节点**：专注于在沙箱中使用特定文件范围编写和修改代码。
- **QA 节点**：检测修改的文件，仅运行相关的测试文件，拦截测试日志，并管理自我反思循环（Self-reflection Loops）。

### 状态与拓扑设计
- 定义统一的 `MultiAgentState` 来传递轻量级元数据（路径、Diff、测试日志），避免传递冗长庞大的文件内容。
- 在 `agent/server.py` 中使用 LangGraph 的 `StateGraph` 来编排各个节点。

### 涉及文件
- [NEW] `agent/multi_agent/state.py` (状态 Schema)
- [NEW] `agent/multi_agent/nodes.py` (PM、架构师和 QA 节点)
- [MODIFY] `agent/server.py` (重新配置状态图以连接所有节点)

---

## 4. 阶段 3：私有 SWE-bench CI-Eval (定量评估指标)

### 核心目标
- 建立自动化、可重复的评估流水线，以基准测试 Pass@1 成功率和 Token 成本。
- 自动从团队的代码库中收集历史 Bug 修复记录。
- 在独立的沙箱中并发运行测试。

### 组件设计
- **数据集生成器**：扫描 Git 历史记录以寻找标记为 Bug 修复的 PR，提取问题描述、`before_sha`（有 Bug 的状态）、`patch`（标准补丁）和具体的测试命令。
- **并发运行器**：使用 `asyncio` 派生并行沙箱，针对 30+ 个测试用例并发运行智能体。
- **评估器**：在智能体运行结束后执行单元测试。计算以下指标：
  - **Pass@1**：成功通过单元测试的用例比例。
  - **成本指数**：每次运行的 Token 消耗和推理成本。
  - **编辑相似度**：将智能体的改动与人类标准补丁进行 AST 结构差异对比。

### 涉及文件
- [NEW] `evals/swe/build_dataset.py` (抓取 Git 历史并导出 `swe_benchmark.jsonl`)
- [NEW] `evals/swe/run_eval.py` (执行并行评估沙箱的运行器)
- [NEW] `evals/swe/config.toml` (评估与裁判设置)

---

## 5. 后续步骤与开发清单

1. [ ] **沙箱安全代理 (阶段 1，里程碑 1)**：
   - 在 `agent/utils/sandbox_safety.py` 中创建 SQLite 数据库初始化和安全辅助函数。
   - 编写关键字列表和基础 LLM 分类函数（使用 `make_model` 或通用客户端）。
   - 实现符合 `SandboxBackendProtocol` 的 `AuditingSandboxWrapper.execute` 方法。
2. [ ] **FastAPI 接口 (阶段 1，里程碑 2)**：
   - 在 `agent/webapp.py` 中实现 `/safety/approvals` 和 `/safety/approve`。
   - 实现 `/safety/approvals/ui` 以渲染一个整洁的 HTML 仪表板（使用 Tailwind/Vanilla CSS），以极具质感的暗黑模式样式展示挂起、批准和阻止的命令！
3. [ ] **CLI approve_cmd 脚本 (阶段 1，里程碑 3)**：
   - 创建 `scripts/approve_cmd.py`。
4. [ ] **集成测试**：
   - 在 `agent/server.py` 中包装沙箱，并进行一次本地模拟运行测试。
