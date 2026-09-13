# ADR：命令层拆分——先抽应用服务，再挪 AstrBot 胶水

- 状态：已落地
- 日期：2026-09-12
- 范围：`src/plugin/steam_status_monitor.py` 的命令面与组合根瘦身；不改会话状态机、轮询语义、Steam / ITAD HTTP 语义
- 对照：`docs/design/command-layer-split.md`（方案与分阶段）；`docs/design/price-and-ranking-ownership.md`（价格编排与排行持有）；`docs/adr/adr-inline-passthrough-helpers.md`（禁止再养空壳转发）
- 落地提交：`75200c6` → `56cfcfb` → `c66c653` → `446564f` → `acf235e` → `aa0ec8d`

本文不替代 `REFACTORING.md`。那份文档记录模块拆分与兼容边界；第 10 节是这次命令层拆分的复盘。本文只覆盖 **「命令入口该装什么、组合根还能剩什么」**。

## 1. 背景

拆分前，`SteamStatusMonitorV3` 同时是：

1. AstrBot `Star` 子类（框架发现入口、命令注册表）
2. 组合根（配置、字体包、落盘、轮询任务、WebAdmin）
3. 价格 / 排行 / 名单 / 启停用例
4. list / who / 测试卡的展示胶水

外部提议把 18 条链路收到 `presentation/commands/`，把插件主体压到 100–150 行。方向对，但有三个硬约束：

1. **AstrBot 只扫描 `Star` 子类上的 `@filter.command` / `@filter.event_message_type`。** 独立函数不会被注册。每个命令至少要留「装饰器 + 签名 + 一行转发」。33 条命令大约 150–250 行，还没算 `__init__` / `terminate`。
2. **`REFACTORING.md` 反对先搬家再分层。** 主体继续保留命令装饰器；后续只在「多个调用方、稳定边界、可独立测试」时再抽。把 `_steam_price` 原样搬进 `store_cmd.py`，得到的是四个仍依赖整个 `self` 的小 God 类。
3. **刚删过透传中介。** 再加一层只 `return await plugin.xxx()` 的 commands，等于把 ADR 刚清掉的跳转加回来。

因此真正要拍板的不是「要不要有 commands 包」，而是：

- 18 条逻辑按入口分包，还是按变化原因分包；
- 100–150 行能不能当验收标准；
- 命令文件、应用服务、组合根各自允许写什么。

## 2. 决策

**先让核心变成可测的 application 服务，再让 commands 做薄适配，最后让 plugin 只注册。四个 `*_cmd.py` 可以当索引，不能当核心的新家。**

具体约束：

| 层 | 允许 | 禁止 |
| --- | --- | --- |
| `plugin/steam_status_monitor.py` | 构造服务、生命周期、`@filter` 注册桩 | 区价循环、时长聚合、裁图、超能力抽取、在线人数 HTTP |
| `application/services/*` | 名单/启停/价格编排/排行记账/读模型 | 直接依赖 `AstrMessageEvent`；自己打 httpx |
| `presentation/commands/*` | 解析 event、调 1–2 个服务、把 Result 变成 `plain_result` / `image_result` | `play_records[...] =`、`httpx`、`ITAD_CLIENT.search_games`、复制 list/who 拼装循环 |
| `presentation/web/` | 继续打 application 服务 | 走 commands 做第二套入口 |
| `presentation/renderers/` | 出图、裁图、超能力文案 | 名单人数上限、日界、区价回退 |
| `infrastructure/` | Steam / ITAD / 字体 / JSON 落盘 | 命令文案、排行展示 |

行数是观察指标，不是目标。主体掉到约 400–500 行可接受；掉到 150 行说明注册桩被藏进了动态注册，审代码和 AstrBot 升级都会更痛。

JSON 仍落在插件 dict（`play_records`、`group_steam_ids`、绑定等）。服务用属性代理读写，避免这一轮再做一次持久化搬家。

## 3. 备选方案

### 3.1 四个命令文件直接承接 18 条逻辑

把 monitor / store / rank / ops 当成最终边界。

否决原因：

- `monitor_cmd.py` 会同时装名单规则和 list 出图；改人数上限会碰到卡片布局。
- `rank_cmd.py` 若只拿「交互部分」，`_record_playtime` 仍留在主类，`SessionService._on_closed` 和 `/steam rank` 继续各碰一份 `play_records`。
- `ops_cmd.py` 会变成垃圾桶。
- Web 后台的 `add_player` 与群命令 `steam_addid` 继续分叉。

### 3.2 命令做成 Mixin，由 `SteamStatusMonitorV3` 多重继承

装饰器可以跟着 Mixin 走。

否决原因：MRO 已经有轮询 / 通知 / 成就 / 持久化 / Steam 客户端。再加四个命令 Mixin，`self` 依赖和初始化顺序会继续膨胀。`QQMenuManagementMixin` 已证明编排可离场，但命令装饰器仍应留在主类，避免「注册表散落在 MRO 各层」。

### 3.3 动态扫描 `presentation/commands/` 自行注册

可以把主体压到 150 行以下。

否决原因：AstrBot 升级、权限装饰器丢失、`steam_allrank` 漏标 ADMIN 都会更难审。`test_modular_structure.py` 现在靠 AST 读主类上的装饰器；动态注册会让这条护栏失效。

### 3.4 每个 HTTP 方法一个 application 包装

例如 `PriceQueryService.search` → `ITAD.search_games`。

否决原因：这是透传中介，和已删的 `MonitorAdminService.resolve_steam_input` 同类。client 已经是稳定边界。价格只抽 **一个用例**（搜索 + 组卡 DTO）；区回退和 DLC 过滤仍在 client。

### 3.5 为了目录对称继续拆 renderers / 引入 DI 容器

否决原因：当前插件单进程、一个 `Star` 实例，`self` 当组合根足够。不为缩短文件或目录好看改成第四种组装方式。

## 4. 所有权

### 4.1 应用服务

| 服务 | 文件 | 持有 / 编排 | 不负责 |
| --- | --- | --- | --- |
| `PriceQueryService` | `application/services/price_query.py` | 译名、搜索、ITAD 无价换区、拼 `PriceCard` | SteamID 解析；client 内部的区回退 / DLC 过滤 |
| `RankingService` | `application/services/ranking.py` | 日界、去重、日聚合、时间片；`SessionService._on_closed` 只调 `record_closed` | 出图、推送范围、`send_message` |
| `RankViewService` | `application/services/rank_view.py` | 补昵称/头像/主玩游戏、昨日推送、`rank_on` | 记账 |
| `MonitorAdminService` | `application/services/monitor_admin.py` | 名单增删、绑定、推送组、清名单；Web 与群命令共用 | 启停水合 |
| `MonitorControlService` | `application/services/monitor_control.py` | `on`/`off`、水合 `skip_push`、成就开关 | 名单 CRUD |
| `PlayerStatusViewService` | `application/services/player_status_view.py` | list / alllist / who 共用读模型 | 出图 |

`resolve_steam_input` 仍在 `SteamClientMixin`。它是玩家身份解析，不是价格链。命令和 Web 在入口处调用一次，得到 17 位 ID 再交给 Admin / openbox。不要再包一层服务去转调它。

### 4.2 命令文件

| 模块 | 入口 | 调用 |
| --- | --- | --- |
| `presentation/commands/monitor.py` | on/off、addid/delid、push、绑定、list/alllist、who、成就开关 | `monitor_control` / `monitor_admin` / `player_status_view` |
| `presentation/commands/store.py` | price/px、game、openbox、候选序号 | `price_query`；译名闭包挂在组合根构造 `PriceQueryService` 时 |
| `presentation/commands/rank.py` | rank/allrank/rank_on | `rank_view`；聚合走 `RankingService` |
| `presentation/commands/ops.py` | config/set/rs、fonts、清缓存、QQ 菜单、测试卡、help | `runtime_config`、已有 `render_*`、`crop_image_auto` |

候选序号事件仍注册在主类，转发到 `store.handle_selection`。help 留在 ops，因为它只是说明书，没有领域状态。

### 4.3 第 4 步下沉的展示辅助

这些不是命令规则，也不该继续挂在 God 类上：

| 符号 | 新家 | 原因 |
| --- | --- | --- |
| `crop_image_auto` | `presentation/renderers/image_crop.py` | 测试卡和开始/结束卡共用的裁图 |
| `SuperpowerPicker` | `presentation/renderers/superpower.py` | 按 SteamID + 日期稳定抽文案；组合根只持有实例 |
| `get_game_online_count` | `infrastructure/clients/steam.py` | Steam 商店 HTTP，属于客户端 |
| `list_parent` | `application/services/steam_list.py` | 列表卡触发者昵称/QQ 头像，list 出图路径消费 |
| `_get_day_key` 回退 | `infrastructure/persistence/plugin_data.py` | 落盘侧读日界；正式日界仍在 `RankingService` |

组合根构造 `PriceQueryService` 时用闭包接 `store.translate_game_query`，避免服务反向依赖命令模块的 LLM 通道。

## 5. 落地步骤与结果

按「先可测边界、再挪胶水」，一次不搬 2000 行：

| 步 | 提交 | 做了什么 |
| --- | --- | --- |
| 1 | `75200c6`、`56cfcfb` | `PriceQueryService`、`RankingService`、`runtime_config`；`MonitorControlService`、`PlayerStatusViewService`；Admin 补 `add_players` / 绑定 |
| 2 | `c66c653`、`446564f` | `RankViewService`；排行推送与名单编排离开主类 |
| 3 | `acf235e` | `presentation/commands/{monitor,store,rank,ops}.py`；`Star` 子类只留注册桩；推送组/清名单并进 Admin，Web 共用 |
| 4 | `aa0ec8d` | 裁图/超能力/在线人数/列表触发者/日界回退离开组合根 |

第 4 步之后，组合根不再包含：

```text
def crop_image_auto
def get_today_superpower
async def get_game_online_count
def _steam_parent
def _get_rank_data
def _record_playtime
def _should_skip_game
async def _daily_rank_push
async def _translate_game_query
```

`_should_skip_game` 的领域实现在 `domain/monitoring`，经 `getattr` 调用，不进 ops。

插件主体最终形态（已落地，不是目标草图）：

```python
class SteamStatusMonitorV3(..., Star):
    def __init__(self, context, config=None):
        apply_runtime_config(self, config)
        self.ranking_service = RankingService(self)
        self.price_query = PriceQueryService(
            self, translator=lambda query: store.translate_game_query(self, query)
        )
        self.monitor_control = MonitorControlService(self)
        self.monitor_admin = MonitorAdminService(self)
        self.player_status_view = PlayerStatusViewService(self)
        self.rank_view = RankViewService(self)
        self.web_api = WebAdminAPI(self)
        ...

    @filter.command("steam addid")
    async def steam_addid(self, event, steamid, at_user="", nickname=""):
        async for result in monitor.addid(self, event, steamid, at_user, nickname):
            yield result
```

注册桩必须留在 `Star` 子类上，这是框架约束。commands 用普通函数，不再包 Command 类。

Mixin（轮询 / 通知 / 成就 / 持久化 / Steam 客户端 / `SessionQuitMixin`）这一轮不动。`SessionQuitMixin` 仍持有 `session_service` 懒初始化和结束卡文案，不具备删除条件。

## 6. 明确不做

- 不为了目录对称再拆 `renderers/`。
- 不把 `SessionService` / 轮询循环塞进 commands。
- 不引入消息总线框架。「总线注册」在这里只指 AstrBot `@filter`。
- 不把 commands 再抽象成 BaseCommand / 依赖注入容器。
- 不在 commands 里为 Web 后台做第二套入口。
- 不把 JSON 持有权从插件 dict 迁走（可后续单独做，不和命令面绑在一起）。
- 不再拆注册桩，也不为压到 150 行做动态注册。
- 不合并 `steam_price` / `steam_px` / `steam_zai_gan_ma`：每个 `@filter.command` 需要独立函数。没有框架 alias 就保持一行转发。

## 7. 后果

### 7.1 正面

- 改 DLC 过滤 / 区价回退只动 client + `price_query` 测试，不动 `monitor.py`。
- 改 list 卡片布局只动 `renderers/steam_list.py`，不动名单人数上限。
- `/steam addid` 与 Web `add_player` 对「已在他群监控」走同一套 Admin 规则。
- `SessionService._on_closed` 与 `/steam rank` 共用 `RankingService`，不再各写一份分钟账本。
- 组合根可审：打开 `steam_status_monitor.py` 能看到构造了哪些服务、注册了哪些命令，看不到区价循环。

### 7.2 代价与残留

- 注册桩仍占主体大部分行数。这是框架税，接受。
- 服务仍接收整个 `plugin`。这是有意识的：状态仍在插件 dict，这一轮只搬编排，不搬存储。后续若要把 `play_records` 真正迁进 `RankingService`，需要同步改 persistence，单独开一轮。
- `store.translate_game_query` 仍在命令模块，因为依赖 AstrBot LLM 通道；组合根用闭包注入，避免 `PriceQueryService` import commands。
- 轮询 / 通知 / 成就 Mixin 仍挂在主类。命令面拆完后不要立刻改成第四种组装。

### 7.3 兼容性

- 对外命令名、权限、文案通道不变。
- 持久化文件格式不变。
- WebAdmin 路由不变，内部改走补全后的 Admin 服务。

## 8. 验收口径

拆完算成功，当且仅当：

1. `presentation/commands/` 里没有 `play_records[...] =`、没有 `httpx`、没有 `ITAD_CLIENT.search_games`。
2. 组合根不再定义第 5 节列出的透传 / 展示辅助。
3. `steam_allrank` / `steam_alllist` 的 ADMIN 装饰器仍在 `Star` 子类上，可被 AST 测试读到。
4. `MonitorAdminService.add_player` 与 `/steam addid` 对跨群占用行为一致。
5. 插件主体不再出现 `_steam_price` 的区价循环、`_get_rank_data` 的聚合循环。

护栏：`tests/unit/test_modular_structure.py` 的 `test_command_modules_do_not_own_core_rules` 与 `test_composition_root_does_not_keep_passthrough_helpers`。

## 9. 验证

相关单测（模块结构、价格区回退、排行、列表路由、超能力、Web 缓存）在落地过程中跑过。代表性命令：

```text
python -m unittest tests.unit.test_modular_structure tests.unit.test_steam_list_routing tests.unit.test_superpower tests.unit.test_store_region_fallback tests.unit.test_price_search -q
```

## 10. 后续才允许碰的事

只有出现新的稳定边界时再动，不要为了目录继续切：

- `SessionQuitMixin`：结束卡文案和 `session_service` 属性迁走之后才能删。
- 排行 JSON 真正迁出插件 dict：必须连 persistence 一起改。
- 同一 handler 多 command：等 AstrBot 提供 alias，再考虑合并 `steam_price` / `steam_px`。
- 并行 sid 队列、`SessionService` 细粒度锁：属于会话并发，与本 ADR 无关。
