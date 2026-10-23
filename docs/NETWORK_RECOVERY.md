# 断网恢复：照搬 WorkBuddy 的 NetworkResume 路线

本文把 WorkBuddy 5.8.3 的断网恢复实现拆成可搬运的部件，并映射到 Open SWE 现有的
桌面端 / 本地后端 / 渲染层结构。结论来自本机安装包（`resources/app.asar` 与
`resources/app.asar.unpacked/renderer/assets`）的代码阅读，以及 Open SWE 本地模式
三种断网方式的实测。

## 0. 结论先行

WorkBuddy **不重连那条已经断掉的流**。它做的是：

1. 把“网络类失败”识别成**可恢复终态**（错误码 3002 / 3003）；
2. **挂住发送队列**，让用户后续输入只排队、不报错；
3. 网络一恢复，**补发一条隐藏的续跑指令**（`请继续执行任务`，标记 `isMeta`）；
4. 用 `rootRequestId / previousRequestId / generation` 把多次尝试**合并成一条回答**，
   并把补发的用户消息在历史里隐藏。

这套结构的好处是它不依赖 TCP 能不能续命，也不依赖 SSE 能不能 replay。

## 1. 实测：Open SWE 本地模式现在的三档断网

| 方式 | 手段 | 结果 |
|---|---|---|
| 渲染层断网 | CDP `Network.emulateNetworkConditions{offline}` | toast「Network connection lost…」与「Back online. Refreshing…」正常；但 `open-swe://` 由主进程 protocol handler 接管，**已在跑的流不受影响** |
| 链路黑洞 | `NtSuspendProcess` 冻结后端 20s | 文本停住 20s，解冻后自动续上并完成；全程无提示、无错误，**无停滞检测** |
| 硬断开 | kill 本地后端 | supervisor 自动拉起新后端（新端口、`/ok` 200），但界面**永久停在 `Working…`**，超过 3 分钟无错误、无 `Reconnecting`、composer 锁死 |

第三档是必须修的：`backendSupervisor.proxy()` 把上游 `Response` 直接返回渲染层，
子进程 `exit` 时没有 abort 在途响应，于是 body 既不结束也不报错，
`useStream` 既不 `onCompleted` 也不 `onReconnect`，`isLoading` 永远为 true。

## 2. 目标与非目标

**目标**

- 断网 / 后端重启 / 链路黑洞都能在可观测时间内收敛到「继续」或「明确失败」，不再永久 `Working…`。
- 网络恢复后**自动接着跑**，用户不需要重新描述需求。
- 历史里不出现重复的用户消息，也不出现「请继续执行任务」这种内部指令。
- 队列里的后续输入在恢复期间不丢、不报错。

**非目标**

- 不做 SSE 级别的断点续传（LangGraph 没有 `Last-Event-ID` 语义）。
- 不保证已随进程消失的 run 的中间状态可恢复：进程死了就是死了，只能重新发起一轮。
- 不改云端 `agent` 图的重试策略，只管本地模式与本地后端的链路。

## 3. WorkBuddy 的参考实现

### 3.1 特权 RPC 与协调器

主进程里有 `startConversationNetworkResumeCoordinator()`，对外只开放四个通道：

```
conversation:armNetworkResume
conversation:disarmNetworkResume
conversation:getNetworkRecoverySnapshot
conversation:networkRecoveryChanged      // 事件
```

非主窗口调用直接 `PERMISSION_DENIED`（"Network resume control is limited to the main window"），
说明这是特权路径，不走通用 daemon RPC 转发。

### 3.2 一条 chain 的状态机

```js
{ rootRequestId, previousRequestId, generation, phase, code, sendTicket, anchorReady, closing }
// phase: waiting → dispatching → inflight，或 delete（inactive）
```

- `arm`：`transition(waiting, {advanceGeneration: true})` 后刷新 anchor；
- `noteWorking`：观察到回合真的在跑——`waiting` 状态下说明**用户自己接管了**（`user-takeover`），
  直接删除 chain，不再补发；`dispatching` 状态下转 `inflight`；
- `complete`：回合正常结束，删除 chain；
- `close` / `disarm`：`closing = true`、`sendTicket++`，需要时取消当前 run。

### 3.3 触发：错误码 3002 / 3003

渲染层的状态扩展 `workbuddy.net-recover.queue` 在回合状态迁移时判定：

```js
if (code === 3002 || code === 3003) {
  return { holdQueue: true, effects: [{ effectId: 'arm', run: () => port.arm({ conversationId, code }) }] }
}
```

- `holdQueue`：队列被 hold，用户后续输入不失败、只排队；
- 其余终态 → `releaseQueue` + `complete`；
- 用户中断 → `releaseQueue` + `close`。

### 3.4 恢复动作：隐藏续跑，而不是重放原 prompt

```js
Pj = [{ type: 'text', text: '请继续执行任务' }]
function hiddenContinueSendOptions(e) {
  return { emitUserMessage: true, _meta: { [a.yr]: e, 'codebuddy.ai': { isMeta: true } } }
}
```

- 文案是**内部指令**，不是用户原话；
- `emitUserMessage: true` 保证它进入会话流（让模型看到），`isMeta` 保证 UI 不展示；
- 走正常 `sendPrompt`，所以能复用服务端已有上下文、工具状态、待确认的权限请求。

### 3.5 触发时机：事件驱动，不是固定退避

```js
reconcileMonitor = () => {
  e.onNetworkOnline(() => { d = Date.now(); ...; e.monitor.poke() })
  e.monitor.watch(flushWaiting)
}

function flushWaiting() {
  // 所有 waiting → dispatching，然后逐条 sendHiddenContinue
}
```

`monitor` 维护 `{ reachable, checkedAt }` 快照，`watch()` 在有 waiting chain 时才订阅，
没有等待者就 `clearTimer()` 退订。恢复信号有两个来源：系统 `online` 事件（`poke()` 立即探测）
和监控自己的可达性翻转。**没有 1s/2s/4s 的等待窗口**，网络一回来就补发。

### 3.6 幂等、防复活、防抢跑

| 机制 | 解决的问题 |
|---|---|
| `sendTicket` + `generation` | 每次补发前校验版本，旧 ticket 的异步结果直接丢弃，不会补发两次 |
| `tombstoneKey(conversationId, rootRequestId)` | 已关闭/已删除会话不会被迟到的补发复活 |
| `noteWorking` 的 `user-takeover` 分支 | 用户自己又发消息，撤销自动补发 |
| `forgetCloudChain` / `transport === 'cloud'` | 只对非 cloud 会话自动续跑；cloud 由服务端负责 |
| `isResolvedApiError` | 补发被服务端拒绝 → 退回 `waiting`，等下一次可达信号，不无限重试 |
| `anchorReady` | 首轮还没有可用 anchor 时先 `refreshAnchor`，用会话最新 request id 作为链锚点 |

日志前缀 `[NetworkResume]`，每次迁移打 `conversationId / epoch / revision / code /
rootRequestId / previousRequestId / generation / phase / network / queue / conversationState`。

### 3.7 历史一致性

补发请求携带谱系元数据：

```js
codebuddy.ai.networkRecovery = { rootRequestId, previousRequestId, generation }
```

配套 relation rule（`ruleId: 'workbuddy.net-recover'`）把它识别为
`kind: 'network-recovery'` 且 `hideUserPrompt: true`；读取时 `mergeChain()` 把同一 root
下多次尝试的 assistant 内容拼成一条，`mergeUsage()` 累加 token 与 cost。

用户看到的是「一条连续回答」，看不到补发指令，也看不到重复提问。

### 3.8 渲染层兜底

- `useOnlineStatus()`：`navigator.onLine` + `online/offline` 事件；
- `isOfflineError(err)`：离线，或 message 命中 `fetch failed / failed to fetch /
  networkerror / ENOTFOUND / ECONNRESET / ETIMEDOUT / EAI_AGAIN`；
- `scheduleRetry()`：`retryAt = now + min(base * 2^(n-1), cap)`，并带 `offlineFailure` 标记；
  **恢复在线时 `failureCount` 归零、`retryAt=0`，立即重试**，不等退避走完。

## 4. Open SWE 现状与缺口

| 能力 | WorkBuddy | Open SWE 现状 |
|---|---|---|
| 失败分类 | 3002/3003 + `isOfflineError` | 只认 `navigator.onLine` 与 SDK 报错 |
| 恢复时机 | 可达性 monitor + `online` 立即 flush | `MAX_RECONNECT_ATTEMPTS=12`，1s→300s 退避，约 23 分钟 |
| 恢复动作 | 隐藏续跑 + 历史合并 | 无 |
| 死链检测 | 有 | 无（实测永久 `Working…`） |
| 队列 | hold / release | 有 store 队列（`queue/<sessionId>`），但无 hold 语义 |

现有落点：

- `desktop/src/backend-supervisor.cjs`：`proxy()` / `request()` / 子进程 `exit` 处理；
- `desktop/src/main.cts`：`serveBundledUi()` 把 `/local-graph/*` 转给 supervisor；
- `ui/src/features/agents/lib/stream/streamPool.ts`：`connection: live | reconnecting`、`reconnectEpoch`、`wakeReconnecting()`；
- `ui/src/features/agents/lib/stream/AgentStreamProvider.tsx`：`useStream({ onReconnect, onConnected, onCompleted })`；
- `ui/src/features/agents/lib/stream/useReconnectNotice.ts`：3s 后才把 `reconnecting` 暴露给 UI；
- `ui/src/lib/networkStatus.tsx`：离线 toast + `refetchQueries` + `wakeReconnecting()`；
- `ui/src/features/agents/lib/stream/localMessageQueue.ts`：`queue/<sessionId>` 存储；
- `agent/middleware/check_message_queue.py`：模型调用前 drain 队列的钩子。

## 5. 目标架构（照搬 WorkBuddy，映射到我们的进程模型）

```
渲染层                                  主进程                                 本地后端
streamPool / networkStatus  ──IPC──▶  NetworkResumeCoordinator  ──HTTP──▶  langgraph dev
  · connection 状态                    · chain 状态机                        · thread/run
  · 队列 hold 展示                     · 可达性 monitor (/ok 探针)            · store Queue
  · 历史合并                           · proxy（abort on exit）
```

### 5.1 新增模块

主进程新增 `desktop/src/network-resume.cjs`，负责 chain 状态机、探针、补发；
`backend-supervisor.cjs` 只负责「把断链变成可见错误」与「重启后端」。
渲染层只消费 `connection` 状态与恢复快照，不自己做重试决策。

### 5.2 数据结构

```ts
interface TurnRecovery {
  threadId: string
  rootRunId: string            // 首次尝试的 run id，合并链的锚点
  previousRunId: string | null // 上一次尝试，第一次为 null
  generation: number           // 每次补发 +1
  phase: "waiting" | "dispatching" | "inflight"
  reason: RecoveryReason       // backend-down | network | run-lost
  sendTicket: number           // 防并发补发
  closing: boolean
  firstWaitingAt: number       // 用于最终兜底超时
}

type RecoveryReason = "backend-down" | "network" | "run-lost"
```

与 WorkBuddy 的 `{rootRequestId, previousRequestId, generation, phase, code, sendTicket, closing}`
一一对应，`code` 换成 `reason`（我们不需要服务端错误码，判定在本地完成）。

### 5.3 状态机

| 事件 | 迁移 | 副作用 |
|---|---|---|
| `classifyRecoverable()` 命中 | `— → waiting` | `holdQueue()`，启动 monitor |
| monitor 报可达 / `online` 事件 | `waiting → dispatching` | `sendTicket++`，补发续跑 |
| 观察到 run 真的在跑（`threads/<id>/state` 有 `next`） | `dispatching → inflight` | 无（用户接管则 `→ delete`） |
| 回合正常结束 | 任意 `→ delete` | `releaseQueue()`，退订 monitor |
| 用户 Stop / 删线程 / 切项目 | 任意 `→ delete` + tombstone | `releaseQueue()`，必要时 abort run |
| 补发被拒（4xx 非网络） | `dispatching → waiting` | 记日志，等下一次可达信号 |
| `firstWaitingAt` 超时（默认 10 分钟） | `→ delete` | 释放队列，UI 显示可重试错误 |

### 5.4 触发与分类

新增 `classifyRecoverable(observation)`，输入是流错误、探针结果、run 状态，输出
`RecoveryReason | null`：

| 观测 | 分类 |
|---|---|
| `navigator.onLine === false` | `network` |
| 错误 message 命中 `fetch failed / failed to fetch / networkerror / ENOTFOUND / ECONNRESET / ETIMEDOUT / EAI_AGAIN` | `network` |
| `GET /ok` 超时或被拒 | `backend-down` |
| 子进程 `exit` 且仍有在途 proxy 响应 | `backend-down` |
| `threads/<id>/state` 里没有 active run，但 UI 认为在跑 | `run-lost` |
| 401 / 403 / 4xx 业务错误 | `null`（终态，不恢复） |

### 5.5 主进程 API 与 IPC

```ts
// desktop/src/network-resume.cjs
createNetworkResumeCoordinator({ probe, resumeRun, logger })
  .arm({ threadId, reason })
  .disarm({ threadId })
  .notifyWorking({ threadId })
  .complete({ threadId })
  .snapshot(): { epoch, revision, chains: Array<TurnRecovery> }
  .onChange(cb)
```

IPC（沿用现有 `desktop:` 前缀）：

```
desktop:network-resume-arm
desktop:network-resume-disarm
desktop:network-resume-snapshot
desktop:network-resume-changed      // main → renderer 推送
```

`networkStatus.tsx` 消费 `desktop:network-resume-changed`，把 `phase` 映射到现有
`StreamConnection`（`waiting` → `reconnecting`，`inflight` → `live`），
不再依赖 `useReconnectNotice` 的 3 秒延迟来「猜」。

### 5.6 可达性 monitor

- waiting 期间每 2s 探一次 `GET http://127.0.0.1:<port>/ok`（1s 超时）；
- 无 waiting 时退订，不空转；
- `online` 事件立即 `poke()`，不等下一个 tick；
- 探针失败 **不**立刻判死，交给状态机等待下一次；
- 每次探针结果写入 `checkedAt`，`snapshot()` 里带上，便于 UI 显示「正在重试（3s 前检测）」。

### 5.7 断链可见化（前置条件）

`backend-supervisor.cjs` 必须先把「静默死链」变成错误，否则状态机永远收不到信号：

- 每个在途 `proxy()` 请求登记一个 `AbortController`；
- 子进程 `exit` 时对所有在途请求 `abort()`，渲染层收到流错误 → `classifyRecoverable` 命中；
- 重启会重新 `reservePort()` 拿新端口（实测 14593 → 10135），渲染层只经 `proxy()` 访问，
  端口变化对 UI 不可见；`request()` 已经是「先 `await this.start()` 再转发」，无需改动。

### 5.8 续跑指令

- 文案进 `agent/resources/prompts/runs/network-resume.md`，用 `load_prompt` 读取，
  不写内联字符串（仓库约定）；
- 发送时带元数据，等价于 WorkBuddy 的 `codebuddy.ai.isMeta`：

```json
{ "_meta": { "open_swe": { "network_recovery": { "rootRunId": "…", "previousRunId": "…", "generation": 2, "isMeta": true } } } }
```

- `agent` 侧读到 `isMeta` 时：不写入用户可见消息、不计入用户轮次统计，
  仅在模型上下文里作为一条系统级续跑指令；
- 补发对象是**新 run**（进程死了就没有旧 run 可续），`previousRunId` 只用于合并展示。

### 5.9 队列 hold

Open SWE 的队列已经是后端 store（`queue/<sessionId>`），drain 发生在
`agent/middleware/check_message_queue.py`。hold 的实现放在渲染层：

- `waiting` 期间 `useLocalPromptQueue` 不入 store、只在本地展示为「排队中」；
- `releaseQueue` 后一次性 drain，合并成一条 prompt（沿用 `readLocalPromptQueue` 的合并逻辑）；
- 释放本身也要能失败：chain 超时释放时后端可能还不通，写入 store 会抛错。这条路径
  **不报错、不丢**——条目保持 `held`，每 3s 重试一次，直到写进 store 由正常 drain 接手
  （重试本身会触发 `request()` 的 `start()`，顺带把后端拉起来）；
- 若 waiting 期间用户手动点了 Stop，队列转 `failed` 展示，允许重发。

### 5.10 静默流看门狗（链路黑洞）

断链有两条入口：**有错误**（子进程 exit → abort 在途响应 → 渲染层收到流错误）和
**没有错误**（后端被冻结、代理把连接吞了、SSE 只剩心跳）。后者今天只有
`useStream` 自己的 12 次退避，最长 23 分钟，中间界面一直 `Working…`。

判定不能放在渲染层：一次跑了三分钟的工具调用和一个死链，在事件流上完全一样。所以：

- 渲染层只报「这个线程的流已经 N 秒没有事件了」（`STALL_QUIET_MS = 60s`，每 5s 检查一次，
  只在 `isLoading` 时检查；进度信号来自 `messages/updates/lifecycle` 事件与
  `messages`/`toolCalls` 的引用变化）；
- 主进程 `desktop:network-resume-stall` 先 `start()` + `probe()` + `threadActivity()`，
  再按 `classifyStall()` 决定：

| 观测 | 动作 | 含义 |
|---|---|---|
| 探针不可达 | `arm` + `poke` | 后端没了，交给 chain（等可达后补发） |
| 可达且线程 `running` | `none` | 只是工具调用太久，别动 |
| 可达但线程不 running / 活动查询失败 | `resync` | 流是死的，`refreshStream()` 立刻重连，不派发第二个 run |

`resync` 只 `reconnectEpoch + 1` 重挂一个实例，不动别的流；重挂后如果线程确实没了，
SDK 会报错，走 §5.4 的 `classifyRecoverable` 进 chain。这条路径让「黑洞」也有了
可观测的收敛时间：60s 探测 + 一次重连，而不是 23 分钟的退避。

### 5.11 历史合并

- `threads.detail` 返回的消息里，`open_swe.network_recovery` 标记为 `meta` 的运行轨迹不渲染；
- 合并规则：同一 `rootRunId` 的多次 assistant 输出按 `generation` 顺序拼接，
  文本边界插入 `\n\n`；usage 累加；
- 用户消息只保留第一条（`hideUserPrompt` 等价物），避免出现两条一样的提问；
- 合并纯函数放在 `ui/src/features/agents/lib/networkRecovery.ts`，输入是消息数组，
  输出是展示数组，单测覆盖：单次尝试、两次尝试、三次尝试、中间尝试无输出。

## 6. 时序

### 6.1 链路黑洞 / 短断网（run 还在）

```
UI                         Coordinator               Backend
 │ submit ───────────────────────────────────────────▶ run 开始
 │ ◀─────────────────────────────────────────────── tokens…
 │ ✕ 探针失败 / 流错误 ──▶ arm(waiting)  ── 2s 轮询 ──▶ /ok 失败
 │    toast「连接中断，恢复后自动继续」
 │                          │                        （run 仍在跑）
 │ ◀── online / /ok 可达 ───┘
 │                         dispatching → 补发（隐藏）
 │ ◀─────────────────────────────────────────────── tokens…
 │                          complete → releaseQueue
```

### 6.2 后端进程重启（run 已丢失）

```
UI                         Coordinator               Backend
 │ ◀── 流错误：socket closed ──▶ arm(backend-down)
 │                          proxy: abort 在途请求
 │                          supervisor.start() ──▶ 新进程 /ok 200
 │                          snapshot: run-lost
 │                          dispatching → 补发隐藏续跑
 │ ◀─────────────────────────────────────────────── 新回答（合并到同一 root）
```

## 7. 数据契约

`desktop:network-resume-snapshot`：

```json
{
  "epoch": "nr-1k2j3-4",
  "revision": 12,
  "chains": [
    {
      "threadId": "7f37e9f7-…",
      "phase": "waiting",
      "reason": "backend-down",
      "rootRunId": "run-1",
      "previousRunId": "run-3",
      "generation": 2,
      "sendTicket": 5,
      "closing": false,
      "firstWaitingAt": 1761130000000,
      "network": { "reachable": false, "checkedAt": 1761130002000 }
    }
  ]
}
```

## 8. 代码落点

| 文件 | 变更 |
|---|---|
| `desktop/src/network-resume.cjs` | 新增：状态机、monitor、补发、快照 |
| `desktop/src/network-resume-run.cjs` | 新增：续跑请求载荷（信封、谱系元数据、config），可单测 |
| `desktop/src/backend-supervisor.cjs` | 在途请求 `AbortController`，`exit` 时 abort；暴露 `probe()` |
| `desktop/src/main.cts` | 装配 coordinator，注册 4 个 IPC，向窗口推送变更 |
| `desktop/src/preload.cts` | 暴露 `networkResume: { arm, disarm, snapshot, onChange }` |
| `ui/src/lib/networkStatus.tsx` | 消费快照驱动 `connection`，替换「3 秒猜」 |
| `ui/src/features/agents/lib/stream/streamPool.ts` | `connection` 增加 `source: "sdk" \| "resume"` |
| `ui/src/features/agents/lib/stream/localMessageQueue.ts` | hold 语义（暂存本地、恢复后合并 drain） |
| `ui/src/features/agents/lib/networkRecovery.ts` | 新增：按 `rootRunId` 合并历史 |
| `agent/resources/prompts/runs/network-resume.md` | 新增：续跑指令文案 |
| `agent/middleware/check_message_queue.py` | 识别 `isMeta` 续跑消息，不打用户轮次 |

## 9. 测试

**单测**

- coordinator：arm → 探针失败 → 探针成功 → 只补发一次（`sendTicket` 隔离）；
- coordinator：`user-takeover` 撤销补发；`closing` 后迟到补发被丢弃；
- coordinator：`firstWaitingAt` 超时后释放队列并上报终态；
- `classifyRecoverable`：6 类观测的判定表；
- `networkRecovery.merge`：1/2/3 次尝试、中间空输出、usage 累加。

**端到端（复用已建好的三种断网脚本）**

1. CDP `Network.emulateNetworkConditions{offline}`：验证 toast、队列 hold；
2. `NtSuspendProcess` 冻结后端 20s：解冻后 `phase` 从 `waiting` 回到 `inflight`，不补发；
3. kill 后端：新进程起来后应补发一次，历史里只有一条提问、一条连续回答。

脚本形态与上一轮一致（PowerShell + CDP over `--remote-debugging-port=9222`），
断言点从「页面文本」改成「`network-resume-snapshot` + 消息数组」。

## 10. 可观测性

结构化日志（静态消息 + `extra`，遵循仓库约定）：

```
"network resume armed"     { threadId, reason, generation }
"network resume probe"     { threadId, reachable, checkedAt, elapsedMs }
"network resume dispatch"  { threadId, sendTicket, generation, outcome }
"network resume released"  { threadId, attempts, totalWaitMs }
```

指标：`resume.armed`、`resume.succeeded`、`resume.rejected`、`resume.timed_out`、
`resume.wait_ms`（直方图）、`proxy.aborted_on_exit`。

## 11. 灰度与回滚

- 开关：`OPEN_SWE_NETWORK_RESUME=0|1`（默认 0），桌面端读进程环境；
- 关闭时行为完全等价于今天：只保留 §5.7 的「abort 在途请求」，因为它修的是卡死，
  不是新功能；
- 回滚只需关开关，chain 状态全在内存，不需要迁移。

## 12. 风险与边界

- **run 无法复活**：本地后端进程死掉后 LangGraph 的 in-flight run 一并消失，我们只能
  重发一轮；`rootRunId` 合并只是展示层连续，不等于执行连续。WorkBuddy 能「续跑」是因为
  daemon 还活着，这一点不要照抄过头。
- **补发可能与工具副作用重复**：如果上一轮已经执行过写文件的工具，重发会让模型再判断一次。
  续跑指令的文案必须显式说明「先检查当前状态，已完成的部分不要重复执行」。
- **探针自身的噪音**：`/ok` 在负载高时可能 1s 超时；用连续 2 次失败才判 `backend-down`，
  单次失败只记日志。
- **队列 hold 的时长**：默认 10 分钟兜底，超过就释放并显示错误，避免无限等待。
- **cloud 会话**：不在本方案内，仍走服务端自己的恢复。

## 13. 分阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | §5.7 断链可见化 + `classifyRecoverable` + 终态错误 UI | kill 后端不再永久 `Working…`，3s 内出现可重试提示 |
| P1 | coordinator + monitor + 快照驱动 UI | 20s 冻结不误判；kill 后端后自动补发一次并完成 |
| P2 | 历史合并 + 队列 hold + 指标 | 同一场景历史里只有一条提问、一条连续回答；队列不丢 |

P0 是修 bug，P1/P2 是照搬 WorkBuddy 的技术路线。建议 P0 先合，独立成立。

## 14. 实施状态

P0、P1、P2 已落地：`desktop/src/network-resume.cjs`（chain 状态机 + 探针 + 补发）、
`backend-supervisor.cjs` 的 `probe()` / 在途 `AbortController`、4 个 IPC 与
`desktop:network-resume-changed` 推送、`ui/src/features/agents/lib/networkRecovery.ts`
（分类 + 历史合并）、`streamPool` 的 `source: "resume"`、`useLocalPromptQueue` 的 hold。

与本文的两处偏差：

1. **开关默认开**：`OPEN_SWE_NETWORK_RESUME` 目前是拒绝式（默认启用，`0|false|no|off`
   关闭）。打包后的桌面端不便传环境变量，默认关闭会让整套恢复在真实安装包里无法生效；
   回滚语义不变，关掉即等价于只保留 §5.7。
2. **不区分 `waiting` 阶段的重连**：SDK 在后端恢复后会重连到线程（不代表 run 还活着），
   因此只有主进程 `threadActivity()` 报 `running` 才判 `taken-over`；渲染层的
   `notifyWorking` 只负责 `dispatching → inflight`。否则后端重启后的自动重连会把待补发的
   chain 误删。

续跑载荷抽到 `network-resume-run.cjs`（信封 / 谱系元数据 / `config.configurable`）并单测：
同时用 Python 侧 `message_sender_id` / `input_message_text` 解析过真实信封，确认
`sender="system:network-resume"`、`kind="system"`、`message_sender_id(kind="human") is None`，
即续跑不会被当成用户轮次，也不影响 `model_selection` / `desktop_branch` 的取值。

`desktop/test/network-recovery.integration.test.cjs` 把主进程那一段串起来跑真实 HTTP：
stub 后端 `/ok` 先 503 再 200，断言 chain 从 `waiting` 走到 `dispatching`，且只发出一条带
信封与谱系元数据的续跑请求（`backend-supervisor` + coordinator + 载荷，不含 Electron/IPC）。

### 真实后端实测（`desktop/scripts/verify-network-recovery.cjs`）

`langgraph dev` + 本地 ollama `qwen3.5:9b`，跑「起 run → 杀掉后端 → 重启 → 补发续跑」，
两次都得到 `NETWORK RECOVERY OK`：线程里 3 条消息（原提问、隐藏信封、AI 回答），
模型接着把被打断的任务做完。实测暴露了三件单测看不到的事，已据此修正：

1. **必须 `multitask_strategy: "interrupt"`**。被 kill 的 run 在 checkpoint 里留下
   `next: ["model"]`，线程从此永远 `busy`；默认 `enqueue` 的续跑会排在一个已经不存在的
   run 后面，实测 120s 仍是 `pending`（队列 `n_pending=2, n_running=1`，`active=0` 死等）。
   `interrupt` 会顶掉那一步，续跑 12s 内 `success`。
2. **线程记录可能整体消失**。另一次重启后 `GET /threads/<id>/state` 直接 404（线程元数据在
   内存里，只有 checkpoint 落在 sqlite）。此时先 `POST /threads {"thread_id": ...}` 用同一个
   id 重建，历史仍在（实测重建后首条 human 消息还在，续跑正常）。
3. **`rollback` 用不了**：本地 checkpointer 没实现 `adelete_for_runs`，带 `rollback` 的请求 500。

### 启动对账（`BackendSupervisor.reconcileOrphanedRuns`）

桌面端重启后 chain 全丢，线程却可能因为快照里那条 `running` 的 run 仍然报 `running`，
`threadActivity()` 于是返回 `running`，续跑判定成 `already-running` 直接放弃。
现在 `startOnce()` 在健康检查通过之后、`start()` resolve 之前发一次
`POST /runs/cancel?action=interrupt {"status":"all"}`：进程刚起来时不可能有 run 在执行，
这个时刻所有 `pending`/`running` 都是孤儿。实测效果是线程状态从 `busy` 变 `error`（不再是
`running`），于是渲染层报错后 chain 能正常补发。

注意这一步**不会**清 `next`：它只是把 run 标成 interrupted。checkpoint 里的
`next: ["model"]` 只有"带 `multitask_strategy: "interrupt"` 的新 run"才会丢掉。所以：

- 补发永远带 `interrupt`，桌面端不依赖"普通 run"去做恢复。
- 若在孤儿线程上发普通 run，LangGraph 会**从 `next` 续跑那一步**，而重建图时拿不到线程的模型
  配置（实测退化成 `ChatAnthropic` 并因缺 API key 直接报错），所以更不能走这条路。
- SDK 的 `submit` 选项虽然类型上有 `multitaskStrategy`，但只有 `"rollback"` 是客户端真实现的，
  渲染层无法在提交时指定 `interrupt`，这也是把对账放在主进程而不是渲染层的原因。

### 已修正的误判：续跑报 Anthropic 认证失败，根因是跨 provider 兜底模型

后端在 `model` 这一步中途死掉、checkpoint 留下 `next: ["model"]`，而快照里**连 run 记录都没有**
（进程在 10s 快照窗口内被杀）时，没有任何对象可供 `interrupt` 取消，于是补发的新 run 会
**接着执行那个 pending 的 `model` 任务**，而不是从头开始。

这条路径原先被判定为「模型路由丢失、落回 `create_deep_agent` 默认模型」。该判断是错的，
同一次运行的日志里有三处互相印证的证据：

- `Using workspace default agent model: model=openai:qwen3.5:9b effort=medium` —— 主模型确实是本地模型；
- `model_call_retry={'failed_on': 'primary', 'fallback_model': 'claude-opus-5', ...}` —— primary
  失败后切到了跨 provider 兜底；
- `model_call_failure={'model': 'claude-opus-5', 'error_type': 'TypeError', 'error_message':
  'Anthropic authentication failed: no API key ...'}` —— 报错来自兜底模型，不是默认模型。

`ChatAnthropic` 是 `ModelFallbackMiddleware` 的兜底实例：`fallback_model_id_for()` 只按 `openai:`
前缀判断，把跑在本机 Ollama 上的 `openai:qwen3.5:9b` 当成了真正的 OpenAI 模型，于是映射到
`anthropic:claude-opus-5`。本地没有 `ANTHROPIC_API_KEY`，而 `TypeError` 不属于可重试异常，
重试在第 2 次尝试就中止，并把兜底模型的错误当成了整个 run 的终态错误。

现在 `agent/dashboard/options.py` 用 `LOCAL_MODEL_IDS` 登记本地模型，`fallback_model_id_for()`
对它们返回 `None`（不跨 provider 降级），`provider_fallback_pair()` 也不会再把云端选择降级到本机模型。
修复后实测（2026-10-23，`node desktop/scripts/verify-network-recovery.cjs`，线程记录 404 路径）：
续跑 `status=success`、耗时 68s、全程 `POST /v1/chat/completions 200`，没有任何 Anthropic 调用。

该次脚本仍判定 `FAILED`，原因是最后一条 AI 消息为空、模型在 Windows 下反复尝试 `ls` 与
`/README.md`。这与模型路由无关，属于恢复后上下文与工具路径的问题，另行处理。
（曾经尝试的 `configurable.checkpoint_id` fork 与 `POST /threads/{id}/state {"as_node": "__end__"}`
都无效——后者直接 400 `Node __end__ does not exist`；定位到真实根因后已不需要它们。）

只要快照里还有 run 记录，`interrupt`（或启动对账里那次 `POST /runs/cancel {"status":"all"}`）
就会把那条 run 标成 interrupted 并清掉 pending 的 super-step，续跑随后是干净的一次执行
——这条路径实测成功过两次。

第二轮补齐（同一轮实现，见 §5.10 / §5.9）：

1. **静默流看门狗**：`desktop/src/network-resume.cjs` 的 `classifyStall()`、
   `desktop:network-resume-stall` IPC、`preload` 的 `networkResume.stall()`、
   `streamPool.refreshStream()`，以及 `AgentStreamProvider` 里的 60s 静默检测。
   黑洞不再只能等 SDK 的 23 分钟退避。
2. **失败收敛**：chain 超时（10 分钟）把连接标成 `failed` 之后，
   `LocalAgentThreadView` 的 `isRunning` 不再采信过期的 `lastActivity` 与 `thread.pending`，
   composer 解锁、`reconnectLabel` 与告警条一起给出「Send a message to pick the task back up」。
   之前这条文案是可读不可执行的：`busy` 一直是 true。
3. **队列释放不丢**：见 §5.9。

§9 的 GUI 端到端与 §10 的部分指标已由
`tests/e2e/playwright.network-recovery.config.ts` 补齐：真实 Electron（local-only 模式）
+ 真实主进程 supervisor / coordinator + 可崩溃、可冻结的 toy LangGraph 后端
（`desktop/test/e2e/fixtures/`），不依赖 PostgreSQL 或 `lsof` / `ps`。
运行 `pnpm --filter open-swe-e2e run test:network-recovery`，指标落到
`artifacts/network-recovery-e2e/*.json`。

实测（2026-10-23，Windows 11；修复后）：

| 场景 | 结果 |
|---|---|
| 渲染层离线（CDP） | 离线提示 8ms、恢复提示 31ms；本地 run 不受影响；0 条恢复链 |
| 后端 kill（run 中途 `os._exit`） | 断链后 **58ms** 建链；**3.1s** 后端重启并派发续跑；**3.9s** 续跑 run 启动；**9.7s** 续跑输出可见；**9.2s** chain 以 `completed` 释放；2 个 run（1 个 resume）、1 条用户消息、隐藏续跑不可见 |
| 后端冻结 64s（`NtSuspendProcess`） | 静默 63.9s 后看门狗建链（60s 静默 + 5s 轮询），解冻后约 0.5s 以 `taken-over` 释放；1 个 run、0 个重复 run；run 正常完成 |

修复前的 kill 场景在 120.6s 内 `revision=0`、0 条链、0 次续跑。根因是渲染层 arm 的唯一
触发是 `stream.error`，而 SDK 的 SSE transport（`maxReconnectAttempts=12`，1s→300s）在断链后
先自行重试约 23 分钟，期间不会设置 `stream.error`；看门狗轮到时
`backendSupervisor.start()` 已经把后端拉起来，`classifyStall` 只会给出
`reachable && 线程不在 running → resync`，而 `resync` 只重挂流、不建链。

本轮修复：

1. **退出即触发**：`BackendSupervisor` 子进程 `exit` → `desktop:local-backend-lost` IPC；
   渲染层 `AgentStreamProvider` 对 `isLoading` 的本地流立即
   `arm({ reason: "backend-down" })`，不再等 SDK 的退避。
2. **stall 区分 run-lost**：`classifyStall` 增加 `threadState`；主进程先查
   `GET /threads/<id>/state`，404 或 `next` 非空返回 `arm("run-lost")`，
   只有 state 完整且 `next` 为空（run 已完成）才 `resync`。
3. **唤醒时序**：`dispatching` 的第一个事件发生在续跑 run POST 之前；此时 remount 会让
   新流 hydrate 404，SDK 会把 pump 永久挂起。chain 快照新增 `dispatched`，渲染层只在
   续跑 run 真正被接受后 `wakeReconnecting()`。

新增单测覆盖整条触发链路：supervisor 退出回调、`classifyThreadState` / `classifyStall`、
渲染层收到 `backend-down` 后对 loading 流 arm（idle 与 cloud 不 arm）、`networkStatus`
只在 `dispatched` 后唤醒、coordinator 在 run 被接受后才把 `dispatched` 置真。
桌面 36 个、UI 58 个相关单测全部通过；该 Playwright 套件 **3/3 通过**。

### 单测与静态检查（本轮）

- `ui`：`networkRecovery`、`streamPool`、`AgentStreamProvider`、`useLocalPromptQueue`、
  `streamMessagesToUi`、`reconnectLabel`、`networkStatus` 全部通过；`tsc --noEmit` 干净。
- `desktop`：`network-resume`、`network-resume-run`、`backend-supervisor`、
  `network-recovery.integration` 全部通过；`tsc -p tsconfig.json` 干净。

