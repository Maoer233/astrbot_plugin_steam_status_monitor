# 游玩会话生命周期重构

本文说明当前监控链路里「一局游戏」如何丢失，以及后续如何用单一会话聚合替换现有的多路径退出逻辑。

本文不替代 `REFACTORING.md`。那份文档记录的是模块拆分与兼容边界；本文只覆盖 **会话所有权**。

本文已吸收 `docs/session-lifecycle-refactor-audit.md`（2026-09-02）的修正：状态机图、所有权粒度、波动语义、止血路径与过渡期双写均以该报告为准。实施时以本文为准，审计报告只作对照。

## 1. 目前存在的问题

### 1.1 用户可见现象

玩家先玩游戏 A，中途切到游戏 B（Steam 摘要里当前 `gameid` 变成 B）：

1. 开始玩 A 时，开始通知正常。
2. 切到 B 时，会推「开始玩 B」。
3. A 的游玩时长、`play_records`、`session_records` 经常不完整，甚至完全没有。
4. 之后关掉 A，也不会再补记 A。Steam 摘要同一时刻只有一个当前游戏，关 A 时系统已经只看得到 B。

纯退出 A（不切到另一款游戏）通常正常。问题集中在 **A → B 切换**。

### 1.2 列表展示脏数据

`/steam list` 计算「已玩多久」时读取 `group_start_play_times[sid]`。退出 A 时不会删除 A 的开始时间，切到 B 后字典里常同时留下 `{A: t0, B: t1}`。

当前实现优先读**当前 gameid** 的起点；`max()` 只在当前 key **缺失**时才会用到。因此「残留 A」本身不会让正在玩 B 的时长接到 A 的起点。脏数据仍会在这些情况下串台：重启丢投影、过滤/跳过写入导致当前 gameid 不在字典里。重构目标是关闭时收回投影，列表只读当前 playing session。

### 1.3 结构问题

文件已经按轮询、状态转换、通知、持久化拆开，但一局游戏的生命周期仍散落在多份可变状态和两个计时器上：

| 状态 | 写入位置 | 作用 |
| --- | --- | --- |
| `group_last_states` | 每次轮询 | 玩家当前摘要 |
| `group_start_play_times` | 开始游戏时写入，退出时经常不删 | 开始时间投影 |
| `group_pending_quit` | 检测循环、延迟任务、180 秒兜底 | 待确认退出 |
| `_pending_quit_tasks` | `asyncio.create_task` | 180 秒后记账 |
| `group_last_quit_times` | 每次 exit 都写 | 主路径几乎不读 |
| `_record_playtime` / `_record_session` | 只在延迟任务里调用 | 真正写排行榜和 session |

结果是：通知可能发出去了，账却没记；或者开始时间还在，会话已经没了。

## 2. 问题原因

根因不是「A 和 B 挤在同一条消息管道里」，也不是缺少 Go channel。A 和 B 已经按 `(group_id, sid, gameid)` 分开存储。真正的问题是：**同一局 A 有两个确认退出的执行者，只有一个会记账；同时 `switch` 被接进了本该只服务「假退出」的 3 分钟缓冲。**

### 2.1 Steam 摘要模型

`GetPlayerSummaries` 每个玩家同一时刻只有一个 `gameid`。从 A 切到 B，对监控来说就是一次状态变化：上一局结束，下一局开始。系统不能再等待「以后关 A」来补记 A。

### 2.2 领域分类和应用层处理不一致

`classify_game_transition()` 返回 `GameTransition`：

- `kind` 五种：`start` / `exit` / `switch` / `unchanged` / `initial`
- `network_fluctuation` 是独立布尔标记，不是 kind

测试也锁定了 `switch` 同时 `has_exit` 和 `has_start`。

但 `check_status_change()` 用 `transition.has_exit` 把 `exit` 和 `switch` 送进同一段逻辑：写入 `pending_quit`，再 `create_task(_delayed_quit_check)`，睡 180 秒。注释写「切游戏也会结算上一款」，实现却是「切游戏也先缓冲」。

网络波动判断只看「当前 `gameid` 是否还在 pending 里」。A → B 时 pending 里是 A、当前是 B，波动逻辑帮不上忙。

### 2.3 两条 180 秒路径抢同一份 pending

延迟任务 `_delayed_quit_check`：

1. `await asyncio.sleep(180)`
2. 若 `pending_quit` 还在且 `notified` 为假，才调用 `_record_playtime` / `_record_session`
3. 再发结束通知并删除 pending

主循环末尾兜底：

1. 扫描所有 `pending_quit`
2. 超过 180 秒且未通知：置 `notified=True`，收集结束通知，**删除 pending**
3. **不调用** `_record_playtime` / `_record_session`

切到 B 后玩家仍「在游戏中」，智能轮询间隔约为 1 分钟，兜底几乎总会先跑。延迟任务醒来看到 `notified` 或记录已删，直接 `return`。

纯退出 A 后轮询变慢（离线档可达数十分钟），延迟任务有机会先跑完，所以「只关 A」不容易复现。

时序：

```text
t=0     开始 A，写入 start_play_times[A]
t=T     摘要变为 B
        has_exit：pending_quit[A] + delayed_task(A, 180s)
        has_start：start_play_times[B]，推送开始 B
        不删除 start_play_times[A]
t=T+60  仍在玩 B，1 分钟一轮询
t=T+180 兜底：notified=True，发结束图，del pending[A]，不记账
t=T+180 delayed_task 醒来：info 不存在或 notified，return
之后    Steam 只显示 B，A 不会再被结算
```

### 2.4 开始时间投影没有随关闭收回

退出分支用 `start_play_times[sid][prev_gameid]` 计算时长后，不 `pop` 该 key。开始分支只写入当前游戏。A → B 后投影残留上一局。

### 2.5 检测循环承担了太多副作用

`check_status_change()` 同时负责：分类、写 pending、取消/创建延迟任务、启动/取消成就任务、计算轮询间隔、拼日志、180 秒兜底、攒通知、存盘。成就轮询和终检也在这里 `create_task`，与「A 是否真正 close」解耦：A 可能没记账，成就终检仍会跑。

### 2.6 为什么 channel 解决不了

Go channel / `asyncio.Queue` 只解决投递，不解决所有权。只要兜底和延迟任务都能消费同一局 A，竞态还在。需要隔离的是 **谁有权 close 这一局，以及 close 时必须记账**。

## 3. 解决方案

### 3.1 目标不变量

一局游戏只有一个所有者：`PlayingSession`。会话按 **`(group_id, sid)`** 持有：同一群的同一玩家同一时刻最多一局 `playing`。多群监控同一 sid 时，各群各持有一份 session，各自 close；`session_id` 幂等去重，避免排行榜重复加分钟。

状态机：

```text
                    observe(gameid=A)
  idle ──────────────────────────────► playing(A)
                                         │
                                         │  observe(gameid=∅)
                                         ├─────────────────────► confirming_exit(A, deadline=t+180)
                                         │                                │
                                         │                      又看到 A  │  deadline 到期
                                         │                      (≤180s)  │  或 observe(B)
                                         │                      resume   │
                                         │                                ▼
                    observe(gameid=B)    │                             closed(A)
                    (switch 立即 close)  │                               │
                                         ▼                               │
                                      closed(A) ─────────────────────────┘
                                         │
                                         │  唯一记账点：
                                         │  _record_playtime + _record_session
                                         │  + SessionClosed
                                         ▼
                                      playing(B)
```

规则：

1. 同一 `(group_id, sid)` 同一时刻最多一局 `playing`。Steam 摘要本来也只有一个 `gameid`。
2. `switch`（一次快照里 A → B）立即 `close(A)`，再 `open(B)`。**不进入** `confirming_exit`，不写 pending。
3. 3 分钟缓冲只用于「`exit` 后 180 秒内回到同一款游戏」。`switch` 立即 close 后不保留 pending；之后再切回 A 算新 `start`。
4. `confirming_exit(A)` 期间若看到 B：立即 `close(A)` 再 `open(B)`，与规则 2 同一条 close。不允许 A 未 close 的同时 B 已 playing。
5. 只有 `close()` 能写 `play_records` / `session_records`，并发出 `SessionClosed`。通知、成就只订阅事件，禁止自己删除 pending。
6. 拉 Steam 的轮询协程只产快照，不直接结算、不发结束图、不改投影。`SessionService.handle(snapshot, now)`（内部 `apply()`）是唯一能 `close()` 的入口。deadline 是 handle 的输入，不是检测循环自己的副作用。
7. 确认退出用 deadline，不用平行的 `sleep` 任务。每轮把 `now` 交给 handle；到期则同一个 `close()`。终态禁止第三条退出路径。

### 3.2 模块切分

沿用现有 `src/domain` / `src/application`，把「检测」和「会话」拆开：

```text
轮询
  └─ PlayerSnapshot (sid, gameid, personastate, ts)

domain/monitoring/session.py
  PlayingSession { id, sid, gameid, started_at, state, exit_deadline }
  apply(session, snapshot, now) -> (new_session, events)

application/services/session_service.py
  持有 sessions[(group_id, sid)]
  调用 apply()
  close 时：_record_playtime + _record_session
  发出 SessionStarted / SessionClosed / NetworkFluctuation

下游（只消费事件）
  notification_tracking
  achievement_tracking
  steam_list          读取 playing.started_at
```

`check_status_change()` 应收成：拉状态 → 分类 → `session_service.handle()` → 收集事件 → 更新 `last_states` / `next_poll`。分类结果交给 handle，检测循环不再自己 close。

现有可保留的部分：

- `GameTransition` 五种 kind + `network_fluctuation` 标记
- `_record_session` 的 `session_id` 去重
- 通知合并缓冲 `_pending_end_notifications`
- `MonitorStateStore` 作为运行时状态入口

必须替换的部分：

- `has_exit` 把 `exit` / `switch` 送进同一套 180 秒缓冲
- 兜底与 delayed task 双确认（终态只留 handle 检查 deadline）
- 退出不删 `start_play_times[A]`
- 成就/通知写在检测循环里

不要引入：Go、每游戏一条 Queue、第三条「以防万一」的退出路径。

### 3.3 与现有代码的对应

| 目标概念 | 现有对应 | 重构动作 |
| --- | --- | --- |
| `PlayingSession.playing` | `start_play_times[sid][gameid]` + `last_states` 有 gameid | 合成一个对象 |
| `confirming_exit` | `pending_quit` + `sleep(180)` | 仅 `kind == exit` |
| `close()` 唯一记账 | `_delayed_quit_check` 与兜底 | 第 0 步合并为同一函数；第 2 步只留 handle |
| `SessionClosed` | `_pending_end_notifications` | 改为单生产者 |
| 波动 resume | `is_network_fluctuation` + 取消 task | 仅 `exit` 后回到同 gameid |
| 幂等 session | `_record_session` 的 `session_id` | 保留；多群各 close 一次靠它去重 |
| 多群记账 | `_record_playtime` 的 5 分钟缓存 | 各群各记一局，`session_id` 幂等；缓存可弱化但暂留 |
| 列表当前时长 | `start_play_times`，缺 key 时 `max()` | 第 2 步改读 `playing.started_at` |
| 成就 | 检测循环 `create_task` | 改为订阅事件 |

最小语义对照：

```text
现在:  has_exit → pending + task
       循环末尾 → 到期则 notified + 通知 + del（不记账）
       task 醒  → 若还在则记账

目标:  kind == exit   → session.confirming_exit(deadline)
       kind == switch → session.close(A); session.open(B)
       confirming_exit 时看到 B → 同上，立即 close(A); open(B)
       handle(now)    → 到期则 session.close()   # 记账 + 发事件一次
       kind == start 且 fluctuation（pending 仍是同一 gameid）→ session.resume()
```

### 3.4 场景时序（实施前验收基线）

三种主路径，每种都应「记账一次、结束通知一次、投影收回」。

**A → B（switch）**

```text
t=T  observe(B)
     close(A)：记账 + SessionClosed + pop start_play_times[A]
     open(B)：playing(B)
     不写 pending，不创建 sleep 任务
```

**A → ∅（exit）再超时**

```text
t=T      observe(∅) → confirming_exit(A, deadline=T+180)
t=T+180  handle 见 now >= deadline → close(A) 一次
```

**A → ∅ → A（≤180s，波动）**

```text
t=T      observe(∅) → confirming_exit(A)
t=T+30   observe(A) → resume playing(A)，不记账、不发结束图
```

补充路径：

| 场景 | 行为 |
| --- | --- |
| `confirming_exit(A)` 时看到 B | 立即 `close(A)` + `open(B)` |
| A → B → A | 第一次 switch 已 close(A) 且不留 pending；再切回 A 是新 `start`，新一局 |
| 多群监控同一 sid，A → B | 各群各 close 一次；`session_id` 相同则 `_record_session` 跳过；`_record_playtime` 暂留 5 分钟缓存 |

### 3.5 分阶段落地

#### 第 0 步：止血（可单独合入）

不新建聚合根，先用现有函数逼近不变量。本步仍读写 `start_play_times`，不引入 `PlayingSession`。

抽出 `_confirm_quit_immediately(group_id, sid, gameid)`：

1. `pending` 不存在或 `notified` 已真 → return（幂等）
2. 必要时重算 `duration_min`
3. 置 `notified=True`
4. `_record_playtime` + `_record_session`
5. 攒结束通知
6. 删除 `pending_quit[sid][gameid]`
7. `start_play_times[sid].pop(gameid)`

调用约定（本步允许两条入口调用**同一个函数**，靠 `notified` 幂等；这是止血，不是终态）：

- `kind == switch`：立即调用，不写 pending，不创建延迟任务
- `kind == exit`：写 pending，仍可创建延迟任务；任务醒来只调用该函数
- 主循环兜底到期：也只调用该函数，不再单独 `notified` + `del` 且不记账

验收：

- A → B：立即记录 A 的时长
- A → ∅：180 秒后记录 A（延迟任务或兜底，二者同一函数）
- A → ∅ → A（≤180s）：取消任务 / resume，不记账
- 兜底与延迟任务不再双记账

本步**不**把「删除兜底」和「兜底也 confirm」并列为终态选项。终态见第 2 步：废弃 delayed task，只留 handle 检查 deadline。

#### 第 1 步：领域状态机

新增 `src/domain/monitoring/session.py`，纯函数 `apply()`，无 asyncio / AstrBot。

此步开始双写：`apply()` 产出 `started_at`；兼容层同步写 `start_play_times`，列表暂仍读旧投影。禁止第三处再改开始时间。

单测覆盖：

- A 开始 → `playing`
- A 退出 → `confirming_exit`；180 秒内回 A → `resume`，不记账
- A 退出超时 → `close`，记账一次
- A → B → 立即 `close(A)` + `open(B)`，A 一定有 session
- `confirming_exit(A)` 时看到 B → 立即 `close(A)` + `open(B)`
- 同一 `session_id` 重复 `close` 幂等
- 多群监控同一 sid：各群各持有 session、各记一次；`session_id` 幂等去重

此步可以先不改 mixin。

#### 第 2 步：应用层换血

`SessionService` 替换 `pending_quit`、`_pending_quit_tasks` 和兜底循环。废弃 delayed task。deadline 只由 `handle(snapshot, now)` 检查并 `close()`。

删除 `start_play_times` 兼容层，列表改读 `session.started_at`。通知只订阅 `SessionClosed`。成就改为在 `SessionStarted` / `SessionClosed` 上启动或终检。

磁盘上 `group_pending_quit` 可先当作旧 `confirming_exit` 读入，再逐步换成 `sessions.json`。`play_records` / `session_records` 格式先不动。

#### 第 3 步：可选并发模型

若以后要并行处理大量 sid，再给每个 sid 一个 `asyncio.Task` + `Queue`。当前一轮询协程串行 `apply()` 足够。过早上 channel 只会把所有权问题藏进队列。

### 3.6 兼容边界

保持：

- 根目录 `main.py` 加载方式
- 现有 `/steam` 命令
- 3 分钟网络波动对外语义（仅「同一游戏假退出」，即 `exit` 后回到同 gameid）
- `session_records` 的 `session_id` 去重
- `play_records` 整数分钟兼容格式
- 多群隔离：会话键仍是 `(group_id, sid)`

改变：

- 切游戏不再走 3 分钟缓冲，A 立即结算
- A → B 后再切回 A：新一局，不算波动
- 结束通知与时长写入绑定到同一次 `close()`
- 列表「已玩多久」最终只读当前 playing session

不自动清理历史里已经丢失的 A 局。若要修旧数据，应先备份，再按 `SteamID + session_id` 审查，无法从 Steam 摘要补回从未写入的局。

### 3.7 明确不要做的事

- 不要引入 Go 或跨语言运行时。
- 不要先加 `asyncio.Queue` 再设计状态机。
- 不要再加第三条退出路径「以防万一」。
- 不要让通知失败回滚时长。
- 不要继续扩展 `start_play_times` 的 int/dict 兼容分支。
- 不要按旧 ASCII 图把 `observe(B)` 送进 `confirming_exit`。
- 不要把会话改成全局 `sessions[sid]`（除非同时重写多群隔离）；当前选定方案 A。
- 不要把 `network_fluctuation` 做成第六种 kind。
- 不要上 Redis TTL 做防刷屏；插件单进程、JSON 落盘，进程内 deadline 即可。

## 4. 建议的下一步

先做第 0 步止血和第 1 步领域单测。这两步不碰渲染和 Steam API，可以独立合入，并作为后续 `PlayingSession` 的验收基线。

实施第 0 步前，用 §3.4 的三条主路径写回归测试，确认每种场景的记账次数、通知次数和 pending / 投影变化。
