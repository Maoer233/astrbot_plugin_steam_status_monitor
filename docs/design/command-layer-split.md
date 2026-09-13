# 命令层拆分：看法与修正方案

- 状态：第 1–4 步已落地
- 日期：2026-09-12
- 范围：`src/plugin/steam_status_monitor.py` 的命令面瘦身；不改会话状态机、轮询、Steam API 语义
- 对照：`REFACTORING.md` 第 5 节「明确不再继续拆的内容」、第 10 节复盘；`docs/adr/adr-inline-passthrough-helpers.md`；落地决策见 `docs/adr/adr-command-layer-split.md`

本文回应「把 18 条链路收到 `presentation/commands/`，插件主体压到 100–150 行」的设计。结论：**方向对，粒度不够，行数目标不现实。** 先拆应用服务，再拆命令胶水；命令文件只做 AstrBot 适配。

## 1. 原方案在做什么

提议目录：

```
src/
├── plugin/steam_status_monitor.py   # 目标 100–150 行：生命周期 + 总线注册
└── presentation/commands/
    ├── monitor_cmd.py               # 逻辑 3, 4, 5, 6, 9, 10
    ├── store_cmd.py                 # 逻辑 7, 8, 15
    ├── rank_cmd.py                  # 逻辑 13 的交互部分
    └── ops_cmd.py                   # 逻辑 11, 12, 14, 16, 17, 18
```

`renderers/`、`formatters/`、`web/` 保持现状。

意图是对的：

- 插件主体现在同时是组合根、命令注册表、价格用例、排行记账、出图胶水。
- 命令确实属于 `presentation`，和 Web 后台、渲染器同层。
- 按用户意图分文件（监控 / 商店 / 排行 / 运维）比「一条命令一个文件」更符合 `REFACTORING.md` 的 KISS。

问题不在「要不要有 commands 包」，而在 **commands 里装什么、插件主体还能剩多少、四份 cmd 文件会不会变成四个小 God 类**。

## 2. 必须先承认的约束

### 2.1 AstrBot 把命令钉在 `Star` 子类上

现有 33 个 `@filter.command` / `@filter.event_message_type` 都写在 `SteamStatusMonitorV3` 上。框架按 **插件类上的方法** 收集 handler，不会扫描 `presentation/commands/` 里的独立函数。

因此「总线注册」只能是下面两种之一，不能假装命令文件自己就是入口：

| 做法 | 结果 |
| --- | --- |
| A. 命令方法留在 `Star` 子类，方法体 `yield from` 到 commands | 插件文件至少「装饰器 + 签名 + 一行转发」× 命令数。33 条命令大约 150–250 行，**还没算 `__init__` / `terminate`**。 |
| B. 把 commands 做成 Mixin，由 `SteamStatusMonitorV3` 多重继承 | 装饰器可以跟着 Mixin 走（当前 `QQMenuManagementMixin` 已证明编排可离场，但命令装饰器仍在主类）。Mixin 一多，MRO 和 `self` 依赖会继续膨胀。 |

无论 A 还是 B，**100–150 行装不下「生命周期装配 + 全部命令注册」**。当前 `__init__` + `terminate` 已超过 200 行：配置迁移、TLS、代理、字体包、落盘、成就监控、轮询任务、WebAdmin。这是组合根该做的事，不该为了行数再压进 commands。

建议把目标改成：

- `steam_status_monitor.py`：**组合根 + 命令注册桩**，大约 **350–500 行** 可接受。
- 每个注册桩：解析 `event`、调一个应用服务、把结果变成 `plain_result` / `image_result`。
- **禁止**在注册桩里写搜索、区价回退、时长聚合、会话水合。

### 2.2 现有拆分原则反对「先搬家再分层」

`REFACTORING.md` 第 5 节写明：主体继续保留命令装饰器、参数校验、用户反馈，以及「需要协调多个应用服务的入口」。后续只在「多个调用方、稳定边界、可独立测试」时再抽。

`adr-inline-passthrough-helpers.md` 补充：不删 AstrBot 注册桩；内联空壳，不新增透传层。

若只把 `steam_addid` / `_steam_price` / `_get_rank_data` 原样搬进 `monitor_cmd.py`，得到的是：

- 四个仍依赖整个 `self` 的文件；
- 多一次跳转，测试仍要起 `Star`；
- Web 后台的 `MonitorAdminService.add_player` 与群命令 `steam_addid` 继续各写一套。

这违反「不为缩短文件而拆」。

### 2.3 18 条链路不是 4 个命令文件能切开的

上一轮归类：

- 纯基建 5：生命周期、陈旧状态、配置热更新、`rs`、字体/缓存。
- 核心业务 7：启停水合、名单 CRUD、推送路由、QQ 绑定、价格规则、排行记账、成就开关。
- 通道与展示 6：list / alllist / 详情卡 / who 卡 / QQ 菜单 / openbox / 测试卡 / help。

原方案把 **核心规则和展示胶水** 按「用户从哪条命令进来」捆在一起：

- `monitor_cmd.py` 同时装名单规则（核心）和 list 出图（通道）。
- `store_cmd.py` 同时装区价/DLC 过滤（核心）和详情卡（通道）。
- `rank_cmd.py` 只拿「交互部分」，记账 `_record_playtime` / `_get_rank_data` 仍留在主类或 mixin，命令层还是要伸手进内部状态。
- `ops_cmd.py` 成为垃圾桶：配置、QQ 菜单、成就开关、三张测试卡、字体、清缓存、help。

按入口分包可以当 **第一刀索引**，不能当最终边界。最终边界必须按变化原因：改价格规则不应碰到帮助文案；改 list 卡片不应碰到 `addid` 人数上限。

## 3. 看法：同意什么，反对什么

**同意**

1. 新增 `presentation/commands/`，把 AstrBot 适配从组合根挪走。
2. 不按「一条命令一个文件」拆。
3. `renderers/` / `web/` 已经够干净，不要再拆。
4. 插件主体最终只做生命周期与注册，不再承载价格/排行实现。

**反对**

1. 把 100–150 行当验收标准。行数是结果，不是目标。
2. 四个 `*_cmd.py` 直接承接 18 条逻辑。那是把 God 类切成四块，核心仍在命令里。
3. 把 `who` / `list` / `alllist` 的「查状态 + 头像框 + 中文名 + 封面」再复制进 commands。这三处已经重复，拆命令前应先收成一个读模型。
4. 再加一层只 `return await plugin.xxx()` 的 commands。ADR 刚删过这类中介。

## 4. 修正后的目标结构

命令文件只认识 **DTO + 应用服务**。规则、HTTP、落盘不进 commands。

```
src/
├── plugin/
│   └── steam_status_monitor.py
│       # 组合根：读配置、构造服务、挂 WebAdmin、拉起轮询
│       # 命令注册桩：@filter.command → commands 模块中的 handler
│
├── application/services/
│   ├── monitor_admin.py          # 已有：名单增删、主从路由（Web 与命令共用）
│   ├── monitor_control.py        # 新增：on/off、水合 skip_push、成就开关
│   ├── binding.py                # 新增：QQ/备注 ↔ SteamID
│   ├── price_query.py            # 新增：译名、搜索、区价回退、锁区/成人内容、DLC 过滤
│   ├── ranking.py                # 新增：日界、去重记账、聚合、push scope
│   ├── player_status_view.py     # 新增：list / alllist / who 共用读模型
│   ├── steam_list.py             # 已有：逐步改成只消费 player_status_view
│   └── openbox.py                # 已有：保持「不依赖 AstrMessageEvent」
│
├── presentation/
│   ├── commands/
│   │   ├── monitor.py            # on/off、addid/delid、push、绑定、list/alllist、who
│   │   ├── store.py              # price/px、game、openbox、候选序号事件
│   │   ├── rank.py               # rank/allrank/rank_on；定时推送的发送侧
│   │   └── ops.py                # config/set/rs、fonts、清缓存、QQ 菜单、测试卡、help
│   ├── renderers/                # 不变
│   ├── formatters/               # 不变
│   └── web/                      # 继续走 MonitorAdminService，不走 commands
│
├── domain/                       # 不变；价格过滤、日界可逐步下沉到这里
└── infrastructure/               # Steam/ITAD/字体/落盘 不变
```

和原方案的差别只有一句：**commands 承接指令胶水，不承接 18 条逻辑。** 18 条里的核心进 `application/services`，通道进 commands + renderers，基建留在 plugin / infrastructure。

### 4.1 每个命令文件允许做什么

允许：

- 从 `AstrMessageEvent` 取出 group_id、原文、@、权限已由装饰器保证的前提。
- 调用一个（偶尔两个）应用服务。
- 把 `Result` 变成 `plain_result` / `image_result` / `base64_image`。
- 写临时 PNG 文件（AstrBot 当前就靠这条通道出图）。

禁止：

- 直接改 `group_steam_ids` / `playing_sessions` / `play_records`。
- 自己打 Steam / ITAD HTTP。
- 复制 `fetch_player_statuses_batch` + 头像框 + 中文名 的拼装循环。
- 实现区价回退、DLC 过滤、日界、去重。

`steam_delid` 已经接近合格形态：解析输入 → `MonitorAdminService.remove_player` → 按 `result.message` 回文案。`steam_addid`、`_steam_price`、`_get_rank_data` 应先长成这样，再搬文件。

### 4.2 插件主体最终长什么样

伪代码，不是实现：

```python
class SteamStatusMonitorV3(
    PollingTrackingMixin,
    StatusChangeTrackingMixin,
    NotificationTrackingMixin,
    AchievementTrackingMixin,
    StateBackedMonitorMixin,
    PersistenceMixin,
    SteamClientMixin,
    Star,
):
    def __init__(self, context, config=None):
        # 配置 / TLS / 字体 / 落盘 / 构造 SessionService 等
        ...
        self.web_api = WebAdminAPI(self)
        self.web_api.register_routes(context)
        self._poll_loop_task = asyncio.create_task(self.global_poll_and_log_loop())

    async def terminate(self):
        ...

    @filter.command("steam addid")
    async def steam_addid(self, event, steamid, at_user="", nickname=""):
        async for result in commands.monitor.addid(self, event, steamid, at_user, nickname):
            yield result
```

注册桩必须留在 `Star` 子类上，这是框架约束，不是设计洁癖。commands 用普通函数即可，不必再包一层 Command 类——否则又是透传中介。

Mixin 方面：`SessionQuitMixin` 若只剩兼容空壳可删；轮询 / 通知 / 成就 / 持久化 / Steam 客户端暂时继续挂在主类，**不要为了目录好看改成第四种组装方式**。组合根一次只改命令面。

## 5. 原四文件映射怎么改

| 原文件 | 原装载 | 修正 |
| --- | --- | --- |
| `monitor_cmd.py` | 3, 4, 5, 6, 9, 10 | 改名 `monitor.py`。handler 只调 `monitor_control` / `monitor_admin` / `binding` / `player_status_view`。`who` 的绑定查找走 binding，出图走 view。 |
| `store_cmd.py` | 7, 8, 15 | 改名 `store.py`。价格规则进 `price_query.py`；`game` / `openbox` 只渲染。候选序号事件仍注册在主类，转发到 `store.handle_selection`。 |
| `rank_cmd.py` | 13 的交互 | 改名 `rank.py`。记账、日界、聚合必须先到 `ranking.py`，否则 `SessionService._on_closed` 和 `/steam rank` 仍各碰一份 `play_records`。定时推送的「算 scope + 渲染 + `send_message`」可留在 rank 命令模块的发送函数，但聚合不能留在主类。 |
| `ops_cmd.py` | 11–12, 14, 16–18 | 允许当运维入口，但成就 **开关** 属于监控策略，应进 `monitor_control`，ops 只转发。测试渲染只调已有 `render_*`，`crop_image_auto` / `get_today_superpower` 下沉到 renderers 或 shared，不要继续放主类。`_should_skip_game` 是领域过滤，进 `domain` 或 session 路径，不进 ops。 |

`help` 可以留在 ops，因为它只是命令说明书，没有领域状态。

## 6. 分阶段，避免一次搬 2000 行

一次把 18 条搬进四个文件，diff 无法审，也必然把核心规则一起拖走。按「先可测边界、再挪胶水」：

进度（2026-09-12）：

- 第 1 步已完成：`PriceQueryService`、`MonitorAdminService.add_players` / 绑定 / 推送路由、`PlayerStatusViewService`。
- 第 2 步已完成：`RankingService` 持有日界/去重/聚合；`RankViewService` 管展示、昨日推送和 `rank_on`。
- 第 3 步已完成：`src/presentation/commands/{monitor,store,rank,ops}.py` 承接胶水；`SteamStatusMonitorV3` 只留 `@filter` 注册桩。
- 第 4 步已完成：`runtime_config.py` 管配置；陈旧状态判断在 persistence。`crop_image_auto` / `SuperpowerPicker` 下沉到 renderers，在线人数进 Steam 客户端，日界/聚合/记账走 `RankingService`，列表触发者信息走 `steam_list.list_parent`。组合根只剩构造、生命周期和注册桩；不要再拆注册桩。

### 第 1 步：抽出已有重复的应用服务（不改目录也能做）

优先这三块，因为已经有两个以上调用方：

1. **名单写入**：把 `steam_addid` 的主监控 / 分发路由 / 人数上限并进 `MonitorAdminService`（`add_player` 已覆盖单条；命令侧是批量 + 绑定 + 自动 on）。Web 与命令必须走同一套，避免再分叉。
2. **价格查询**：只抽一个用例 `resolve_game` / `build_card`，细节见 `docs/design/price-and-ranking-ownership.md`。不要包 `resolve_steam_input`，不要在应用层重复 client 已有的区回退和 DLC 过滤。
3. **玩家状态读模型**：`list` / `alllist` / `who` 共用「批量状态 + 绑定名 + 会话时长 + 头像框」。`handle_steam_list` 已有雏形，把 `alllist` / `who` 的复制循环收进去。

验收：相关单测不需要 `AstrMessageEvent`；命令函数变薄但暂时仍可留在主类。

### 第 2 步：排行榜记账离开主类

`play_records`、`session_records`、`_recorded_quit_cache` 由 `RankingService` 持有；`SessionService._on_closed` 只调 `record_closed`。持有权理由见 `docs/design/price-and-ranking-ownership.md`。

这是核心业务，不是 commands 的事。提前做，是为了 `rank.py` 搬家时没有私货可搬。

### 第 3 步：新建 `presentation/commands/`，按四个入口搬家

此时每个 handler 应已是「解析 → 服务 → 出图/回文」。搬家是机械的，review 只看有没有夹带规则。

命令注册桩留在 `steam_status_monitor.py`。不要在这一步追求 150 行。

### 第 4 步：组合根再瘦

把 `__init__` 里成块的「读配置赋属性」收成 `load_runtime_config(config)`；陈旧状态判断跟 persistence。插件文件只剩：构造、注册 Web、拉任务、`terminate`、命令桩。

展示辅助不要再挂组合根：裁图和超能力进 renderers，在线人数进 Steam 客户端，日界/聚合走 `RankingService`。

若仍觉得命令桩占行，保持现状。那是框架税，删不掉。

## 7. 明确不做

- 不为了目录对称去拆 `renderers/`。
- 不把 `SessionService` / 轮询循环塞进 commands。
- 不引入消息总线框架（「总线注册」在这里只指 AstrBot `@filter`）。
- 不把 commands 再抽象成 BaseCommand / 依赖注入容器。当前插件是单进程、一个 `Star` 实例，`self` 当组合根足够。
- 不在 commands 里为 Web 后台做第二套入口。Web 继续打 application 服务。

## 8. 验收口径

拆完算成功，当且仅当：

1. 改 DLC 过滤 / 区价回退只动 `price_query`（及测试），不动 `monitor.py`。
2. 改 list 卡片布局只动 `renderers/steam_list.py`，不动名单规则。
3. `MonitorAdminService.add_player` 与 `/steam addid` 对「已在他群监控」的行为一致。
4. `presentation/commands/` 里没有 `play_records[...] =`、没有 `httpx`、没有 `ITAD_CLIENT.search_games`。
5. 插件主体不再出现 `_steam_price` 的区价循环、`_get_rank_data` 的聚合循环。

行数只作为观察指标。主体掉到 500 行以下即可；掉到 150 行说明注册桩被藏进了动态注册，审代码和 AstrBot 升级都会更痛。

## 9. 一句话

原方案把「命令入口」当成了「逻辑归属」。正确顺序是：

**先让 18 条里的核心变成可测的 application 服务，再让 commands 做薄适配，最后让 plugin 只注册。**

四个 `*_cmd.py` 可以当索引，不能当核心的新家。
