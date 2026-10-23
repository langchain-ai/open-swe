# Agent 测量口径与源码改动计划

本文固定后续源码改动的测量口径：**先声明要移动的指标，再用同一套数据源和实验协议验证**。目标不是追求好看的数字，而是让每个对外数字（PR、汇报、简历）都能追溯到任务集、样本量、重复次数和原始结果。

关联文档：[AGENT_RELIABILITY.md](AGENT_RELIABILITY.md)（可靠性实现与边界）、[evals/agent/README.md](../evals/agent/README.md)（评测运行方式）。

## 1. 适用范围

适用于以下方向的源码改动：

- 主 Agent 图、上下文压缩、模型路由与回退
- 沙箱生命周期、启动、恢复、凭证与网络策略
- 需求澄清、Plan 模式、验收标准
- Webhook / 调度 / 入口幂等
- 评测 harness、分析事件与看板

纯展示层改动如果没有可观测行为变化，可以跳过实验，但必须在 PR 中声明"无可测指标变化"，不能引用其他改动的数字。

## 2. 指标口径

### 2.1 上下文压缩

**数据源 A：分析事件 `context.offloaded`**（生产与本地运行均可用）

| 字段 | 类型 | 定义 |
|---|---|---|
| `trigger` | `manual` / `automatic` / `overflow` | 手动 `/offload`、阈值自动触发，或 provider 拒绝超预算调用后先压缩再重试 |
| `status` | `completed` / `failed` | 摘要成功或失败（失败时没有 `summary_tokens`） |
| `cutoff_index` | int? | 被摘要替换的前导消息数（状态坐标） |
| `messages_summarized` | int? | 本次送入摘要的消息条数 |
| `summarized_tokens` | int? | 被替换消息的 token 估计（middleware tokenizer） |
| `summary_tokens` | int? | 摘要文本的 token 估计 |
| `duration_ms` | int? | 摘要模型调用耗时，失败也记录 |

**数据源 B：评测图状态 `conversation_offloading`**（离线评测，跨压缩累计）

| 字段 | 定义 |
|---|---|
| `count` | 本次 run 内完成的压缩次数 |
| `overflow_count` | 其中由 context overflow 触发的压缩次数 |
| `total_messages_summarized` | 累计被摘要的消息条数 |
| `total_summarized_tokens` | 累计被替换 token 估计 |
| `total_summary_tokens` | 累计摘要 token 估计 |
| `total_duration_ms` | 累计摘要耗时 |

**派生指标（离线与线上同一公式）：**

- `compaction_rate` = 至少压缩过一次的 run 数 / 评测 run 数
- `mean_compactions` = 总压缩次数 / 评测 run 数
- `mean_compaction_summarized_tokens` = `Σsummarized_tokens` / 评测 run 数
- `mean_compaction_summary_tokens` = `Σsummary_tokens` / 评测 run 数
- `overflow_compaction_rate` = `overflow_count > 0` 的 run 数 / 评测 run 数
- `mean_overflow_compactions` = `Σoverflow_count` / 评测 run 数
- `compression_ratio` = `Σsummarized_tokens / Σsummary_tokens`；只有大于 1 才说明净节省上下文
- 生产聚合接口：`GET /dashboard/api/analytics/context-offloaded?period=7d|30d|all`；响应含 `compaction` 总计与 `by_trigger` 分项（`overflow` 是独立分项）

**压缩策略实验条件（eval-only）：** `AgentEvaluationConfig.compaction` 控制触发维度（`trigger_tokens` / `trigger_messages` / `trigger_fraction`，只能选一个）与摘要提示词变体（`default` / `structured`）。结构化摘要提示词位于 `agent/resources/prompts/compaction/structured-summary.md`。该配置只经评测 `configurable` 传入，生产运行保持模型默认策略。

**口径限制：**

- token 数来自 tokenizer 估计，**不是计费 token**；不得与 LangSmith cost 相加，也不得替代 provider 账单。
- 本次改动之前写入的事件没有新字段，聚合时按 0 计；引用历史区间时必须注明字段从 2026-10-23 起可用。
- `compression_ratio` 只衡量"摘要替代了多少原文"，不衡量摘要质量；质量必须由任务成功率或返工率佐证。

### 2.2 任务结果（沿用现有定义，不做二次解释）

定义位置：`evals/agent/judge.py::summarize_results`。

- `resolve_rate`：`fail_to_pass` 全部通过且 `pass_to_pass` 不回退的实例占比
- `patch_applied_rate`、`error_rate`、`mean_fail_to_pass_rate`、`mean_pass_to_pass_rate`
- `failure_rate.<stage>`：12 类失败阶段各自占比；阶段迁移必须逐题解释
- `median_steps`、`median_wall_clock_seconds`、`usage_complete_rate`
- 压缩相关：第 2.1 节派生指标；评测结果逐题保存 `compaction` 原始值

### 2.3 规划中的指标（实现时先补定义再写代码）

| 方向 | 拟定指标 | 数据源（待建） |
|---|---|---|
| 沙箱启动 | 冷启动 P50 / P95 | prepare 阶段打点（create → ready） |
| 沙箱恢复 | `sandbox_unreachable` run 失败率、恢复成功率 | 失败事件 + 恢复日志 |
| 沙箱回收 | 孤儿沙箱数、闲置计费时长 | provider 元数据 + 生命周期事件 |
| 入口幂等 | 重复 run 率、故障注入下事件丢失率 | webhook delivery id + run 创建记录 |
| 需求澄清 | 澄清率、澄清轮次、一次通过率、返工率 | `task.accepted` / `task.rework_requested` |

指标名一旦在此登记，不得在报告中改名或改变分母。

## 3. 实验协议

1. **固定条件**：模型 ID、reasoning effort、沙箱类型/镜像、依赖安装命令、任务集 SHA-256 指纹。不同条件的运行不得直接比较。
2. **样本**：正式实验至少 10 题；每题先验证"参考补丁 resolved、空补丁 no_patch"；任务集必须覆盖目标行为（例如压缩实验必须让任务跨过压缩阈值）。压缩实验使用 `evals/agent/instances/long_context.jsonl`，通过 `AGENT_EVAL_COMPACTION_TRIGGER_*` 固定触发条件；若试跑未触发压缩，按 [LONG_CONTEXT_TASKS.md](../evals/agent/LONG_CONTEXT_TASKS.md) 调整阈值并在报告中注明实际维度与数值。
3. **重复**：每组至少 3 次；报告每次的结果，不只报最好的一次。
4. **配对**：同一任务集、同一评分策略，使用 `evals/agent/compare.py` 逐题比较；报告 improved / regressed / unchanged、失败阶段迁移、paired delta（百分点）。
5. **报告必含**：N、重复次数、模型与沙箱版本、任务集指纹、原始结果目录、指标定义引用（第 2 节）。
6. **禁止**：只报均值不报分母；把 smoke/自检当成模型性能；跨任务集比较；删题；把离线结论写成线上结论。

运行方式：

```bash
# 配对比较（两次已完成的本地或 LangSmith 运行）
uv run python -m evals.agent.compare \
  --baseline artifacts/<baseline-run> \
  --candidate artifacts/<candidate-run> \
  --output-dir artifacts/<compare-run>
```

## 4. 源码改动验收门槛

每个涉及行为的源码 PR 必须满足：

- **G1 假设先行**：PR 描述写明"哪个指标应变化、预期方向和最小可接受幅度"。新指标先在本文登记。
- **G2 遥测先行**：行为改动之前先有可采集字段；遥测改动要兼容旧数据（缺字段按 0/None 处理），并有 schema 测试。
- **G3 相关测试**：运行 focused 测试并通过；新增行为有行为测试；不跑全量测试（项目约定）。
- **G4 回归门禁**：正式评测（N≥10 ×3）中，`resolve_rate` 相对基线的配对下降不得超过 5 个百分点；若预期无变化（重构），失败阶段分布不得出现新的集中失败。超过门槛必须在 PR 中解释并给出原因。
- **G5 性能类**：延迟指标报告 P50/P95 与样本数（≥20）；吞吐类报告并发度。
- **G6 成本类**：报告每次成功任务的成本与用量覆盖率；压缩/子代理/重试的估计值必须单独列出，不能混入主 Agent 计费 token。
- **G7 结论措辞**：只有实测数字可以写成效果；工程事实（新增字段、覆盖阶段、测试数）与效果数字必须分开表述。

样本不足（N<10 或重复<3）时，只能声称工程事实和方向性观察，**不得**出现"提升 X%"。

## 5. 改动流程

1. **声明假设**：在 issue/PR 中写清指标、方向、测量方法。
2. **补遥测**：先落地事件/状态字段与测试，合并后再改行为。
3. **测基线**：固定条件跑 baseline，保存原始结果与任务集指纹。
4. **实施改动**：保持单一变量；混合改动无法归因。
5. **配对实验**：baseline 与 candidate 各 3 次，输出 `comparison.json` / `report.md`。
6. **记录结论**：结果写入 `evals/agent/*_RESULTS.md` 或对应设计文档，注明 N、重复次数、失败阶段迁移。
7. **对外表述**：按第 4 节 G7 与第 6 节规则写 PR 描述、汇报和简历。

## 6. 对外表述规则

- 每个数字必须能回答：**指标定义（本文哪一节）、样本 N、重复次数、运行环境**。
- "成功率提升 X%" 需要：正式任务集（N≥10）、3 次重复、配对比较、失败阶段解释；缺一不可。
- 单次 smoke、参考补丁自检、空补丁自检不得作为性能证据。
- 开源贡献要写清个人边界：在 Open SWE 上做了哪些提交、哪些能力是平台原有；不要把平台能力写成个人成果。
- 推荐句式：`在 <N> 题任务集（<模型/沙箱>，3 次重复，配对比较）上，<指标> 从 <a> 变为 <b>（<±Δ> pp）；<机制> 由 <实现> 保证。`

## 7. 当前状态与路线图

| 阶段 | 内容 | 状态 | 退出条件 |
|---|---|---|---|
| 1 | 压缩遥测：事件字段、图状态累计、聚合查询/API、评测采集与聚合指标 | 已完成（2026-10-23） | focused 测试、ruff、ty 通过；旧事件兼容 |
| 2 | 长上下文任务集：任务跨过压缩阈值；压缩策略 A/B（触发阈值、结构化摘要、溢出逃生） | 进行中（2026-10-24）：任务集与策略开关已完成并通过自检；正式 N≥10 ×3 配对实验待跑 | N≥10 ×3 配对实验，产出 failure-stage 迁移报告 |
| 3 | 沙箱：启动打点、回收计数、凭证白名单、不可达恢复 | 待开始 | 指标登记 + 前后对比 |
| 4 | 需求澄清：结构化提问工具、验收标准工件、反馈回灌 | 待开始 | 人工标注集 + 线上返工率口径 |
| 5 | 入口幂等与持久队列；线上成功率看板 | 待开始 | 故障注入测试 + 看板查询 |

阶段 2 的实现与实验说明见 [evals/agent/LONG_CONTEXT_TASKS.md](../evals/agent/LONG_CONTEXT_TASKS.md)：任务集指纹、reference/empty 自检结果、arm 环境变量与正式运行命令都记录在那里。任务集自检只能证明题目可评分，不能作为模型效果或压缩收益证据。

## 8. 实现位置索引

| 能力 | 位置 |
|---|---|
| 压缩事件定义 | `agent/analytics/events.py`（`ContextOffloadedPayload`） |
| 压缩事件记录 | `agent/analytics/usage.py::record_context_offloaded` |
| 压缩中间件与累计状态 | `agent/middleware/conversation_offloading.py` |
| 压缩策略配置（eval-only） | `agent/evaluation.py::CompactionConfig`、`agent/server.py::_compaction_settings` |
| 结构化摘要提示词 | `agent/resources/prompts/compaction/structured-summary.md` |
| 生产聚合查询 | `agent/analytics/queries.py::context_offloaded_summary` |
| Dashboard API | `agent/analytics/routes.py`（`/analytics/context-offloaded`） |
| 评测采集 | `evals/agent/target.py::_GraphResult`、`evals/agent/models.py::CompactionTelemetry` |
| 评测聚合 | `evals/agent/judge.py::summarize_results` |
| 配对比较 / 冻结回放 | `evals/agent/compare.py`、`evals/agent/regressions.py` |
| 长上下文任务集 | `evals/agent/instances/long_context.jsonl`、`evals/agent/LONG_CONTEXT_TASKS.md` |
