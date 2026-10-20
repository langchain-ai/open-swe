# Agent 运行可靠性与失败回放

这轮工作按“环境标准化 → 超时取消 → 失败回归 → 幂等一致性”推进，
在既有编码 Agent 评测链路上补齐四个方向的第一版实现。
代码和测试是实现依据；这里的故障场景来自本地复现与代码检查，
没有将其当作已经发生的线上用户事故。

## 6. 环境标准化：先记录实际环境，再约束命令

此前本地模型运行曾在 Windows `cmd.exe` 中反复尝试 POSIX 命令和错误路径，
35 轮、344.7 秒后仍未产出有效补丁。历史记录见
[SMOKE_RESULTS.md](../evals/agent/SMOKE_RESULTS.md)。

现在桌面运行及 Agent 评测会在选中的沙箱内部探测操作系统、命令执行方式、
Python 版本及路径、工作目录和 Git 版本。探测结果进入图状态
`execution_environment`，随评测结果保存；模型按 `cmd`、`posix` 或 `unknown`
接收对应指导，原有结构化系统消息内容保留。
探测不到可执行程序时保留失败信息，不把服务端操作系统当作沙箱操作系统。
桌面命令环境也补齐 `SystemRoot`、`COMSPEC`、`PATHEXT` 等 Windows 必需变量。

评分器默认使用自身的 `sys.executable` 执行 pytest，并将其所在目录加入安装命令的
PATH；可用 `AGENT_EVAL_PYTHON` 显式指定其他解释器。
这修复了实际回放中误选系统 Python、因缺少 pytest 而改变失败分类的问题。
没有解析到任何测试结果的非零退出归为 `test_execution`。

入口代码：`agent/sandboxes/environment.py`、
`agent/middleware/execution_environment.py`、`evals/agent/judge.py`。

**当前边界：**这是环境探测、执行指导和解释器对齐，尚未提供固定 Linux 镜像、
每题独立依赖环境或可重建容器。基准实验仍需自行固定模型、依赖和沙箱镜像。
Windows 适配后的模型成功率尚未重新测量。

## 1. 超时取消：为模型、工具和本地进程设定执行边界

`RunDeadlineMiddleware` 给一次主 Agent invocation 建立共享截止时间，
模型调用、模型回退重试和工具调用消耗同一预算；工具内的委派任务也在工具预算内。
截止时间写入图状态，同一 invocation 从 checkpoint 恢复时继续使用原截止时间；
新的 invocation 重新分配预算。升级前没有截止时间的 checkpoint 在首次操作时
获得预算，无法补算升级前已经耗费的时间。

```powershell
$env:OPEN_SWE_RUN_TIMEOUT_SECONDS = '3600'
```

默认 3600 秒，只接受正数且有限的值。耗尽时抛出终止型
`RunDeadlineExceeded`，取消正在等待的异步处理器，并拒绝后续模型或工具操作。
它与服务商自身的请求超时区分开，避免总预算耗尽后继续触发模型回退。
既有 `OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS` 继续约束单次模型调用。

桌面和本地 provider 使用原生异步 shell backend。命令输出边读边排空，
只保留设定字节数，避免大输出阻塞管道或无限增长内存；命令超时返回退出码 124，
保留超时前输出。外部取消清理进程树后继续抛出 `CancelledError`。
Windows 使用 Job Object，POSIX 使用独立进程组。
评分器的 Git、依赖安装和测试命令也采用同样的 Windows 进程归属机制，
避免依赖 `taskkill` 枚举进程。正常完成的本地命令允许显式启动的后台进程继续运行。

入口代码：`agent/middleware/run_deadline.py`、
`agent/sandboxes/local_execution.py`、`agent/sandboxes/windows_job.py`。

**当前边界：**共享预算覆盖模型和工具执行，没有包住整个 graph factory 或所有
准备节点；异步取消需要下游正确响应取消。远端沙箱的命令终止取决于 provider。
Windows 创建进程到加入 Job Object 之间仍有很短的竞态窗口，这不构成操作系统级
沙箱保证。恢复后的绝对截止时间还依赖机器时钟正常同步。

## 10. 失败回归：冻结产物，重复评分

从本地评测运行中显式选择题目，冻结输入、评分规则、原始预测、预期修复结果和
失败阶段，以及源任务集 SHA-256。回放沿用同一个确定性评分器，不调用模型。

```powershell
.venv/Scripts/python.exe -m evals.agent.regressions freeze `
  --run-dir artifacts/local-eval-empty `
  --instance-id self-check__calc-add `
  --output artifacts/frozen-empty-regression.json

.venv/Scripts/python.exe -m evals.agent.regressions replay `
  --cases artifacts/frozen-empty-regression.json `
  --output-dir artifacts/replay-new
```

示例依赖本机仍存在的任务仓库及原始报告。换机器需要准备可访问的仓库和依赖。
输出目录和冻结文件必须使用新路径，避免覆盖原始证据。
回放写出 `cases.json`、`results.jsonl`、`summary.json`、`report.md` 和
`expectations.json`。修复结果或失败阶段与预期不同，CLI 退出码为 1。
运行被中断时保留未完成报告。

实际使用空补丁案例进行了真实 Git checkout 和 pytest 回放。初次回放暴露了解释器
选择问题；修复后 `artifacts/replayed-empty-regression-fixed/expectations.json`
记录 `matched: true`，预期与实际均为 `no_patch`。
这是评分链路自检，不能计作模型成功率。

入口代码：`evals/agent/regressions.py`。

**当前边界：**回放重新评分已存预测，不重演原始模型与工具调用轨迹；
不会重试历史服务商故障来证明故障已经恢复。案例可能包含代码、提示和完整日志，
目前没有自动脱敏；本地产物位于忽略目录，纳入仓库或分享前需人工审查。

## 4. 幂等一致性：先收紧 Slack 入口去重

重复 webhook、跨 worker 同时处理、共享存储故障和写成功但响应丢失，
都会影响是否创建重复任务。本版围绕这些入口场景补齐行为：

- 以 `channel:message_ts` 作为消息唯一领取键，没有消息标识时回退到 `event_id`。
  合并同一消息的不同投递形式，避免先后领取两个远端键造成部分成功。
- 通过 LangGraph thread 的原子创建冲突裁决跨 worker 领取；每次尝试带随机所有权
  token，写入响应丢失后回读，确认已落盘记录是否属于本次尝试。
- 共享存储操作最多等待 5 秒。无法确定领取结果时返回 HTTP 503 和
  `Retry-After: 1`，不继续放行；外层 Slack 失败处理器透传这一异常，避免转成 200。
- 本地 LRU 缓存最多 2048 个键，并采用与远端一致的 600 秒有效期。
  远端 I/O 放在进程缓存锁外，慢事件不阻塞无关消息的领取。

入口代码：`agent/slack/events.py`、`agent/slack/failures.py`。

**当前边界：**这是 10 分钟窗口内的入口去重，不保证端到端 exactly-once。
领取成功后、任务真正执行前，进程仍可能崩溃；无法判定的远端写结果也可能需要
对账恢复。已经先返回 200 的后台路径不能靠后续抛出异常请求 webhook 重试。
PR、评论等外部副作用没有新增业务幂等记录。
下一步应围绕“领取 → 持久化待执行任务 → 消费 → 完成/补偿”加入事务投递或
可恢复领取协议，再覆盖重启恢复和副作用去重。

## 证据与简历

验证包括真实本地命令、进程取消后子孙进程无法延迟写文件、正常后台进程存活、
图内模型及工具超时传播、checkpoint 恢复、存储故障与恢复、入口 503 透传，
以及冻结案例的分类漂移检测。仅运行与改动相关的测试，不运行全套测试。
最新检查记录与简历写法见 [LOCAL_RESULTS.md](../evals/agent/LOCAL_RESULTS.md)。

当前可以陈述异步执行、取消传播、失败证据、回归回放与入口去重的实现；
尚无改动后真实模型基准数据，不能填写成功率、延迟或成本改善百分比。
