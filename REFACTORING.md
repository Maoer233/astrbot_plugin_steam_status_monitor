# 模块拆分与影响范围说明

## 1. 背景

原插件将 AstrBot 入口、命令处理、轮询调度、状态转换、通知、Steam API、数据持久化、图片渲染和管理接口集中在根目录 `main.py` 中。问题不在于文件行数本身，而在于不同变化原因共享同一个模块：修改排行榜可能影响轮询，修改网络请求可能触碰命令入口，核心逻辑也难以脱离 AstrBot 环境单独测试。

本次拆分遵循以下原则：

- **KISS**：只拆分职责明确、可独立验证的逻辑，不以缩短文件为目标。
- **内联优先**：仅由一个入口使用、且与命令流程紧密相关的短逻辑继续留在插件主体中。
- **兼容优先**：保留根目录入口、现有命令、配置字段和本地数据目录。
- **渐进迁移**：共享运行状态通过兼容属性代理到统一状态容器，避免一次性改写全部调用方。

## 2. 最重要的拆分

### 2.1 插件入口与主体分离

- 根目录 `main.py` 仅作为 AstrBot 兼容入口。
- 插件主体迁移至 `src/plugin/steam_status_monitor.py`，继续负责生命周期、命令注册和跨模块编排。

**目的**：保持 AstrBot 的加载方式不变，同时让业务代码进入清晰的包结构。

**影响范围**：插件导入路径和内部维护入口发生变化；用户命令、配置方式及数据位置不变。

### 2.2 监控状态统一归属

`src/domain/monitoring/state.py` 引入 `MonitorStateStore`，集中管理：

- 分群 SteamID；
- 上次玩家状态；
- 游戏退出时间；
- playing_sessions；启动时一次性 hydrate 旧 pending_quit / start_play_times 文件；
- 下一次轮询时间；
- 启动阶段状态和待发送通知。

`StateBackedMonitorMixin` 为旧字段提供属性代理，因此现有业务仍可使用原字段名称。

**目的**：明确共享可变状态的唯一所有者，降低多个服务直接在主类上任意创建状态字段造成的耦合。

**影响范围**：只调整运行时状态的组织方式，不改变持久化 JSON 的位置和格式。

### 2.3 监控应用服务拆分

以下流程从插件主体迁移到 `src/application/services/`：

| 模块 | 职责 |
| --- | --- |
| `polling_tracking.py` | 启动初始化、全局轮询调度、跨群 SteamID 批量去重查询、到期会话 tick |
| `status_change_tracking.py` | 状态快照投递；会话所有权交给 SessionService |
| `session_service.py` | 一局游戏的唯一入口：apply、记账、开始/结束通知、成就启停 |
| `session_quit.py` | 结束卡文案与 SessionService 入口 |
| `notification_tracking.py` | 通知聚合与发送 |
| `achievement_tracking.py` | 成就变化跟踪与结算 |
| `monitor_admin.py` | 名单增删、绑定、推送组、清名单；Web 与群命令共用 |
| `monitor_control.py` | `/steam on|off`、水合 `skip_push`、成就开关 |
| `price_query.py` | 译名、搜索、ITAD 无价换区、拼价格卡 |
| `ranking.py` | 日界、去重、日聚合；会话 close 只调 `record_closed` |
| `rank_view.py` | 排行展示补齐、昨日推送、`rank_on` |
| `player_status_view.py` | list / alllist / who 共用读模型 |
| `qq_menu_management.py` | QQ 菜单相关管理流程 |

**目的**：让轮询、状态转换、通知和管理操作可以分别阅读与测试，减少修改其中一个流程时对其他流程的影响。

**影响范围**：内部方法所属模块变化，但仍由插件主体统一编排；命令名称和通知入口不变。

### 2.4 领域规则独立

`src/domain/monitoring/` 保存不依赖 AstrBot UI 的核心规则：

- `polling.py`：轮询间隔和下一次轮询时间计算；
- `transitions.py`：开始、退出、切换及网络波动状态分类；
- `state.py`：监控运行状态模型。

`src/domain/ranking/push_scopes.py` 负责排行榜推送范围计算。

**目的**：把“如何判断”与“如何发送消息、如何调用接口”分开，使关键规则能够通过普通单元测试验证。

**影响范围**：状态判断和调度计算的调用位置变化；对外表现保持兼容，本次同时修正了网络波动字段使用错误。

### 2.5 基础设施与展示层拆分

- `src/infrastructure/clients/steam.py`：Steam API、游戏名称、在线人数和媒体资源请求。
- `src/infrastructure/persistence/plugin_data.py`：配置、状态、游玩和 Session 数据读写。
- `src/presentation/commands/`：AstrBot 命令胶水（monitor / store / rank / ops）。
- `src/presentation/renderers/`：开始、结束、列表和排行榜图片渲染；裁图与超能力文案。
- `src/presentation/web/`：AstrBot 管理页面路由、统计构建和缓存。
- `src/shared/`：路径、日志、网络及通用辅助能力。

**目的**：隔离外部 I/O 与展示细节，让应用流程不再同时承担 HTTP、文件和绘图实现。

**影响范围**：内部调用路径变化；Steam API、管理页访问方式、图片输出和数据目录不变。

## 3. 与本次修复的关系

### 3.1 多游戏排行榜显示

排行榜数据由“玩家只关联一个游戏”调整为“玩家总时长 + 游戏明细列表”。同一玩家在统计周期内的多款游戏分别聚合和渲染，排名仍按总时长计算。该变化影响：

- 本群排行；
- 全群排行；
- 日榜、周榜、月榜和总榜；
- 每日自动推送；
- 排行榜图片卡片布局。

不影响状态监控、通知和 Steam 绑定数据。

### 3.2 时长累计修复

Session 记录使用业务日期、开始时间和游戏 ID 组成稳定的 `session_id`。同一个 SteamID 即使被多个群监控，同一局游戏也只写入一次；时长以两位小数保存，避免短时 Session 被截断。

网络波动恢复改为读取领域转换结果中的专用标记，并及时清理待退出状态，避免误判为新 Session 后重复结算。

该变化影响后续新增的 `session_records` 及基于其构建的统计，不会自动清理历史重复记录。历史数据如需修复，应先备份，再按 `SteamID + session_id` 去重，并同步校正或重建 `play_records`。

## 4. 兼容边界

本次拆分保证：

- 根目录 `main.py` 仍是插件加载入口；
- 现有 `/steam` 指令及主要参数不变；
- 配置字段和本地数据目录不主动迁移；
- 旧字段通过状态代理继续可用；
- `play_records` 保留原有整数分钟兼容格式；
- 不加入 Fork 专属或实验性功能。

需要注意：

- `session_records` 去重只对修复后的新写入生效；
- 同一 Session 当前只保留首次成功写入的 `group_id`，全局统计正确，但历史按群归属若要求一条 Session 同时属于多个群，后续应引入兼容的 `group_ids` 模型；
- 跨午夜 Session 仍沿用现有业务日期口径，本次没有改变自然日拆分规则。

## 5. 明确不再继续拆的内容

命令层拆分（2026-09-12）之后，插件主体只保留：

- 插件初始化和生命周期编排（组合根）；
- AstrBot `@filter` 注册桩（装饰器 + 签名 + 一行转发）；
- 轮询 / 通知 / 成就 / 持久化 / Steam 客户端 Mixin，以及仍持有 `session_service` 的 `SessionQuitMixin`。

参数校验、用户反馈和通道胶水已在 `src/presentation/commands/`。名单、启停、价格编排、排行记账、读模型已在 application 服务。不要再为缩短文件去拆注册桩，也不要把 commands 再抽象成 BaseCommand / 动态注册。

后续只有出现新的稳定边界时才继续抽取，例如 JSON 持有权迁出插件 dict（必须连 persistence 一起改），或 `SessionQuitMixin` 的结束卡文案迁走之后才能删除。

会话生命周期见第 6 节。命令层拆分复盘见第 10 节。

## 6. 会话状态机显式状态重构（2026-09-02）

### 6.1 背景与目标

原有会话管理存在以下问题：

1. **A→B 切换丢失时长**：`switch` 被接入 180 秒缓冲，延迟任务和兜底逻辑抢同一份 `pending_quit`，导致通知发出但未记账
2. **双路径确认退出**：`_delayed_quit_check` 任务和主循环兜底各自处理到期，只有一个会记账
3. **所有权不明**：检测循环、延迟任务、通知模块都可能修改 `pending_quit` 和 `start_play_times`

重构目标：
- **唯一所有者**：一局游戏只有一个 close 入口
- **立即结算**：A→B 立即 close(A) + open(B)，不进入 180 秒缓冲
- **明确消抖**：180 秒只用于「exit 后回到同一游戏」
- **终态统一**：废弃延迟任务，用主轮询 deadline 检查

### 6.2 设计方案

采用**显式状态方案**（方案 A）：

```python
@dataclass(frozen=True)
class PlayingSession:
    sid: str                        # Steam ID
    gameid: str                     # 游戏 ID
    started_at: int                 # 开始时间（秒级时间戳）
    state: str                      # "playing" / "confirming_exit" / "closed"
    group_id: str                   # 所属群组（多群隔离）
    exit_deadline: Optional[int]    # 消抖截止时间（confirming_exit 时有值）
    exited_at: Optional[int]        # 退出时间
    closed_at: Optional[int]        # 关闭时间
```

**状态转换规则**：

| 场景 | 行为 | 事件 |
|------|------|------|
| idle → A | `open(A)` | SessionStarted |
| playing(A) → ∅ | `state="confirming_exit", exit_deadline=now+180` | 无 |
| confirming_exit(A) → A（180s内） | `state="playing", exit_deadline=None` | NetworkFluctuation |
| confirming_exit(A) → 到期 | `close(A)` | SessionClosed |
| playing(A) → B | 立即 `close(A)` + `open(B)` | SessionClosed + SessionStarted |
| confirming_exit(A) → B | 立即 `close(A)` + `open(B)` | SessionClosed + SessionStarted |

**核心不变量**：

1. 同一 `(group_id, sid)` 同一时刻最多一局 `playing`
2. `switch` 立即 close，不进入 `confirming_exit`
3. 180 秒缓冲只用于「exit 后回到同一 gameid」
4. `confirming_exit(A)` 时看到 B：立即 close(A) + open(B)
5. 只有 `SessionService._on_closed()` 能记账
6. 轮询只产快照，`SessionService.handle()` 唯一能 close
7. 用 `tick_due()` 检查 deadline，不用延迟任务

### 6.3 实现细节

**领域层**（`src/domain/monitoring/session.py`）：

```python
def apply(
    session: Optional[PlayingSession],
    snapshot: Mapping[str, Any],
    now: int,
    *,
    sid: str = "",
    group_id: str = "",
) -> Tuple[Optional[PlayingSession], Tuple[SessionEvent, ...]]:
    """纯函数状态机：idle / playing / confirming_exit / closed。
    
    只有这里会把一局标成 closed。switch 立即 close，不进 confirming_exit。
    3 分钟缓冲只用于 exit 后回到同一 gameid。
    """
```

**应用层**（`src/application/services/session_service.py`）：

- `handle()`：调用 `apply()`，分发事件，启动成就轮询
- `tick_due()`：每轮检查 `confirming_exit` 且 `now >= exit_deadline` 的会话，调用 `apply()` 触发 close
- `_on_closed()`：唯一记账点，调用 `_record_playtime()` 和 `_record_session()`
- `_on_started()`：发送开始通知，启动成就轮询
- `_on_resumed()`：发送网络波动恢复通知

**检测循环集成**：

- 轮询只产 `PlayerSnapshot`，调用 `SessionService.handle()`
- 每轮调用 `SessionService.tick_due(now)` 检查到期会话
- 不再直接操作 `pending_quit` 和 `start_play_times`

**持久化**：

- 新增 `playing_sessions.json`：序列化所有 `playing` 和 `confirming_exit` 的会话
- 启动时从 `playing_sessions.json` 加载，如无则从旧数据 `hydrate_from_legacy()`
- `play_records` 和 `session_records` 格式不变

### 6.4 方案对比（显式 vs 隐式状态）

文档同时记录了**隐式状态方案**（方案 B）供参考：

```python
# 隐式状态：3 字段
{
    "current_game": "730",          # 当前游戏（消抖期间不变）
    "started_at": 1725235200,       # 开始时间
    "debounce_deadline": None       # None=playing, 有值=confirming_exit
}
```

**对比**：

| 维度 | 显式状态（方案 A） | 隐式状态（方案 B） |
|------|-------------------|-------------------|
| 字段数 | 5-8 个 | 3 个 |
| 状态清晰度 | ✅ `state` 字段显式 | ⚠️ 通过 `debounce_deadline` 推导 |
| 类型安全 | ✅ 类型系统保证转换合法 | ⚠️ 可能误用字段 |
| 可读性 | ✅ 状态机一目了然 | ⚠️ 需要理解隐式约定 |
| 易扩展 | ✅ 直接加状态枚举 | ⚠️ 新增状态需要更多字段组合 |

**选择理由**：
- 可维护性优先于字段数
- 状态机显式，降低团队协作成本
- 类型提示保证状态转换正确性
- 便于未来扩展（如 `paused` 状态）

**注**：两种方案本质相同，核心不变量一致，团队可根据实际情况选择。详见 `docs/session-lifecycle-refactor.md` §3.1 和 §3.3。

### 6.5 影响范围

**改变**：
- 切游戏（A→B）立即结算 A，不再走 180 秒缓冲
- A→B→A 是新一局，不算网络波动
- 结束通知与时长写入绑定到同一次 `close()`
- 运行时废弃 `pending_quit`、`_pending_quit_tasks` 和 `start_play_times`；启动仍可一次性 hydrate 旧文件

**保持**：
- 180 秒网络波动对外语义（仅「exit 后回同一 gameid」）
- `session_records` 的 `session_id` 去重
- `play_records` 整数分钟兼容格式
- 多群隔离：会话键 `(group_id, sid)`
- 根目录 `main.py` 和现有命令

**兼容性**：
- 启动时从旧 `pending_quit` 和 `start_play_times` hydrate
- 列表「已玩多久」改读 `session.started_at`
- 通知、成就改为订阅 `SessionStarted` / `SessionClosed` 事件

### 6.6 验证场景

重构后通过以下场景验证：

1. **A→B（switch）**：立即 close(A) + open(B)，记账一次，通知两次
2. **A→∅→A（180s内）**：resume playing(A)，不记账，发波动通知
3. **A→∅→到期**：close(A)，记账一次，通知一次
4. **confirming_exit(A)→B**：立即 close(A) + open(B)
5. **多群监控同一 sid**：各群各 close 一次，`session_id` 幂等去重

## 7. 验收与后续

### 7.1 已完成（第 0-3 步）

- ✅ 抽出 `_confirm_quit_immediately()` 统一记账路径
- ✅ 领域层纯函数 `apply()` 状态机
- ✅ `SessionService` 接入检测循环，废弃延迟任务
- ✅ `tick_due()` 替代 asyncio.sleep
- ✅ 持久化 `playing_sessions.json`
- ✅ 运行时删除 `group_pending_quit` / `_pending_quit_tasks` / `group_start_play_times`
- ✅ 启动仍可一次性 hydrate 旧 `pending_quit` / `start_play_times` 文件，之后不再回写

### 7.2 可选后续

- 并行 sid 队列（非必须）

### 7.3 不做的事

- 不引入 Go 或跨语言运行时
- 不先加 `asyncio.Queue` 再设计状态机
- 不再加第三条退出路径「以防万一」
- 不把会话改成全局 `sessions[sid]`（保持多群隔离）
- 不上 Redis TTL 做防刷屏

## 8. 验证范围

本次改动通过单元测试和编译检查覆盖以下关键场景：

- 同一 Session 在多个群触发时只记录一次；
- Session 小数分钟精度；
- 多游戏排行榜聚合和展示结构；
- 游戏状态转换及网络波动语义；
- 轮询计算、分群状态和模块导入结构；
- 持久化、网络、管理接口缓存及基础渲染路径；
- **会话状态机**：A→B 立即结算、exit 后恢复、消抖到期、多群隔离。

模块拆分的目标不是改变插件功能，而是在保持兼容的前提下缩小修改的影响面，并为数据正确性修复提供可测试的边界。

---

## 9. 参考文档

- **会话方案**：本地 `docs/self/` 中的会话生命周期稿（不上传）
- **命令层决策**：`docs/adr/adr-command-layer-split.md`
- **命令层方案**：`docs/design/command-layer-split.md`
- **价格与排行持有**：`docs/design/price-and-ranking-ownership.md`
- **透传中介**：`docs/adr/adr-inline-passthrough-helpers.md`

---

## 10. 命令层拆分复盘（2026-09-12）

### 10.1 发生了什么

拆分前，`SteamStatusMonitorV3` 同时是 AstrBot `Star` 子类、组合根、价格/排行/名单用例，以及 list / who / 测试卡的展示胶水。外部提议把约 18 条链路收到 `presentation/commands/`，把插件主体压到 100–150 行。

方向对，但有三个硬约束：

1. AstrBot 只扫描 `Star` 子类上的 `@filter.command`。独立函数不会被注册。33 条命令至少要留「装饰器 + 签名 + 一行转发」。
2. 第 5 节原先反对先搬家再分层。把 `_steam_price` 原样搬进 `store_cmd.py`，只会得到四个仍依赖整个 `self` 的小 God 类。
3. 刚删过透传中介。再加一层只 `return await plugin.xxx()` 的 commands，等于把空壳跳转加回来。

拍板：**先让核心变成可测的 application 服务，再让 commands 做薄适配，最后让 plugin 只注册。** 行数是观察指标，不是验收标准。

### 10.2 实际落地

按「先可测边界、再挪胶水」，一次不搬 2000 行：

| 步 | 提交 | 做了什么 |
| --- | --- | --- |
| 1 | `75200c6`、`56cfcfb` | `PriceQueryService`、`RankingService`、`runtime_config`；`MonitorControlService`、`PlayerStatusViewService`；Admin 补 `add_players` / 绑定 |
| 2 | `c66c653`、`446564f` | `RankViewService`；排行推送与名单编排离开主类 |
| 3 | `acf235e` | `presentation/commands/{monitor,store,rank,ops}.py`；`Star` 子类只留注册桩；推送组/清名单并进 Admin，Web 共用 |
| 4 | `aa0ec8d` | 裁图/超能力/在线人数/列表触发者/日界回退离开组合根 |

结果：

- 插件主体约 370 行，只剩构造、生命周期和注册桩。
- commands 里没有 `play_records[...] =`、没有 `httpx`、没有 `ITAD_CLIENT.search_games`。
- `/steam addid` 与 Web `add_player` 对「已在他群监控」走同一套 Admin 规则。
- `SessionService._on_closed` 与 `/steam rank` 共用 `RankingService`。
- 护栏：`tests/unit/test_modular_structure.py` 用 AST 读主类装饰器，并禁止组合根再定义透传辅助。

### 10.3 否掉的做法

| 方案 | 否决原因 |
| --- | --- |
| 四个命令文件直接承接 18 条逻辑 | 名单规则和 list 出图捆在一起；Web 与群命令继续分叉 |
| 命令做成 Mixin 多重继承 | MRO 已有轮询/通知/成就/持久化；注册表会散落各层 |
| 动态扫描 commands 自行注册 | 权限装饰器丢失更难审；AST 护栏失效 |
| 每个 HTTP 方法一个 application 包装 | 透传中介，client 已经是稳定边界 |
| 为目录对称再拆 renderers / 引入 DI | 单进程一个 `Star` 实例，`self` 当组合根足够 |

### 10.4 学到什么

- **按变化原因分包，不要按入口分包。** 用户从哪条命令进来，不等于规则该住在那个文件。
- **框架税要写进验收，不要当技术债去还。** 注册桩占主体大部分行数是 AstrBot 约束，压到 150 行只会把注册藏起来。
- **先抽可测服务，搬家才是机械的。** 第 3 步能一次搬完，是因为第 1–2 步已经把核心从 handler 里掏空。
- **JSON 持有权不要和命令面绑在一起。** 服务仍用属性代理读写插件 dict。真正迁 `play_records` 必须连 persistence 一起改，单独开一轮。
- **commands 允许有胶水，不允许有核心规则。** `who` 解析 CQ、测试卡调 `render_*`、`/steam rs` 清状态，都是通道。区价回退、时长聚合、名单上限不是。

### 10.5 残留与下一轮

这一轮到此为止，不再继续拆命令层。残留不是没做完：

- 注册桩必须留在 `Star` 子类。
- `store.translate_game_query` 仍在命令模块，因为依赖 AstrBot LLM；组合根用闭包注入。
- Mixin（轮询 / 通知 / 成就 / 持久化 / Steam 客户端 / `SessionQuitMixin`）不动。
- JSON 仍落在插件 dict。

下一轮该盯的是会话细粒度锁、成就补偿、JSON 持有权，不是再搬命令。决策全文见 `docs/adr/adr-command-layer-split.md`。
