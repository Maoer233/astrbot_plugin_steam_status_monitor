# 价格查询依赖与排行榜状态持有

- 状态：已落地（`PriceQueryService` / `RankingService` 持有权按本文执行；推送范围后由 `RankViewService` 承接）
- 日期：2026-09-12
- 对照：`docs/design/command-layer-split.md`、`docs/adr/adr-command-layer-split.md`、`docs/adr/adr-inline-passthrough-helpers.md`

本文回答三件事：价格链为什么显得复杂、`resolve_steam_input` 实际被谁调用、`play_records` / `_recorded_quit_cache` 该由谁持有。决策已随命令层拆分落地；下文保留拆分前路径作为对照。

## 1. `resolve_steam_input` 不是价格链，也不是数十处调用

实现在 `SteamClientMixin`（`src/infrastructure/clients/steam.py`）。职责是把好友码 / 主页链接 / vanity / SteamID64 收成 17 位 ID。这是 **玩家身份解析**，和游戏 AppID、ITAD、区价无关。

生产代码里的调用点只有 5 处：

| 位置 | 用途 |
| --- | --- |
| `steam_addid` | 批量名单 |
| `steam_delid` | 删人 |
| `steam_openbox` | 开箱 |
| `admin_api._api_groups_add` | Web 加玩家 |
| `admin_api` 批量导入 | 逐行解析 |

`MonitorAdminService.resolve_steam_input` 已经按 ADR 删掉，那是死转发，不是新边界。

不要做的事：

- 不要为了「统一入口」再包一层 Admin / Price 服务去转调它。
- 不要把它塞进价格查询。价格要的是 AppID，不是 SteamID64。
- 不要因为探索代码时工具命中多次，就当成运行时扇出很大。

边界：命令和 Web **在入口处**调用一次，得到 `sid` 再交给 `MonitorAdminService` / `handle_openbox`。解析失败在入口回文案，服务只认已经合法的 17 位 ID。

## 2. 价格查询：复杂的是编排，不是再缺一层服务

### 2.1 现在实际走的路径

`_steam_price` 一条命令里串了整条用例：

```
消息原文
  → extract_price_query / 候选序号缓存          # 通道
  → AppID 链接？ITAD.lookup_steam_appid
    否则 LLM 译名 + ITAD.search_games           # 搜索（ITAD 内部还会再搜 Steam 商店）
  → ITAD.get_price_summary + 区回退             # 史低
  → Steam.fetch_region_price × N               # 对比区；客户端内部再区回退
  → Steam.fetch_game_details                   # 详情；客户端内部再区回退 + 语言回退
  → Steam.fetch_game_reviews_both
  → 锁区文案 + render_game_detail_image         # 展示
```

`ITADClient.search_games` 自己已经：Steam 商店本地化搜索 → 拉英文标题 → ITAD 标题匹配 → DLC/bundle 排序。命令再套一层 LLM 译名，等于 **两条搜路叠在一起**。

区回退也叠了三层：

1. 命令里对 ITAD summary 按 `store_region_candidates` 再打。
2. `fetch_region_price` 内部再按候选区打 Steam。
3. `fetch_game_details` 内部再按候选区 + 简体/英文打 Steam。

所以「依赖链复杂」是真的，但原因不是缺少 `PriceQueryService` 这个文件名，而是：

- 编排住在命令方法里，读代码要从 `_steam_price` 跳进两个 client；
- 搜索和区回退在命令与 client 各做一遍；
- `/steam game` 又单独打 `fetch_game_details` + 同一张渲染卡，和价格卡只差 ITAD/区价。

### 2.2 不该怎么拆

不要做「每个 HTTP 方法一个 application 包装」：

```
PriceQueryService.search → ITAD.search_games
PriceQueryService.details → Steam.fetch_game_details
PriceQueryService.region  → Steam.fetch_region_price
```

那是透传中介，和已删的 `MonitorAdminService.resolve_steam_input` 同类。client 已经是稳定边界，也有单测（`test_price_search.py`、`test_store_region_fallback.py`）。

### 2.3 该怎么收

只抽 **一个用例**，返回渲染 DTO，不返回 httpx Response。

```
PriceQueryService
  resolve_game(query) -> GameRef | list[GameRef] | NotFound
  build_card(game, *, currency, region, compare_region) -> PriceCard
```

| 层 | 留下什么 |
| --- | --- |
| 通道 | 剥命令前缀、候选序号会话、出图、锁区那一行商店链接 |
| 应用 | 要不要 LLM 译名、多候选时取第一条还是列出、ITAD 无价时要不要换区、拼 `PriceCard` |
| 基建 | Steam / ITAD HTTP、商店搜索、appdetails、评测、DLC 过滤、单次请求的区/语言回退 |

硬规则：

1. **区回退只在 client 内循环。** 应用层最多「ITAD 这个 country 没价，换下一个 country 再调一次 `get_price_summary`」。不要在命令里再 `gather(fetch_region_price)` 套一层候选区。
2. **DLC 过滤只留在 `ITADClient`。** 应用不要重写标题匹配。
3. **LLM 译名视为可选前置，默认可关。** `search_games` 已走中文商店索引。译名与商店本地化命中打架时（艾尔登法环 → 英文 token → 只剩 DLC）已经出过缺陷。新服务应：无中文直接搜；有中文先 `search_games(原文)`，空结果再译。不要无条件先译。
4. **`/steam game` 走 `build_card` 的子集**（无 ITAD、无对比区），不要第三套详情拉取。
5. **候选序号缓存留在 presentation。** 那是对话状态，不是价格域。

验收：改 DLC 规则只动 `itad.py`；改「中文先搜再译」只动 `PriceQueryService`；改卡片布局只动 `renderers/game_detail.py`。

## 3. 排行榜状态：谁持有

### 3.1 现在谁在碰

| 状态 | 语义 | 写入 | 读取 | 落盘 |
| --- | --- | --- | --- | --- |
| `play_records` | 按日聚合 `{date: {sid: {gameid: {name, minutes}}}}` | `plugin._record_playtime`，由 `SessionService._on_closed` 触发 | `/steam rank`、每日推送、Web 统计补洞 | `play_records.json`，保留 30 天 |
| `session_records` | 单局时间片，甘特/热力 | `PersistenceMixin._record_session`，同一 `_on_closed` | Web 统计主数据源 | `session_records.json`，保留 90 天 |
| `_recorded_quit_cache` | `{(sid, gameid): ts}`，5 分钟内不重复加分钟 | 仅 `_record_playtime` | 仅自己 | **不落盘** |

日界 `_get_day_key`（凌晨 4 点切日）被排行聚合、session 写入、每日推送、Web 今日统计共用，却定义在插件上。

### 3.2 决策

**`play_records` 和 `_recorded_quit_cache` 都由 `RankingService`（应用层）持有。**  
`session_records` 一并放进同一个服务。  
`SessionService` 不持有、不直接改这三份结构。  
插件只做组合根：构造服务、启动时 `load`、脏标记时交给 persistence 写盘。

理由：

1. **和会话聚合根不是同一件事。** 会话键是 `(group_id, sid)`，一局游戏一个所有者。排行键是全局 `sid`（外加日期、gameid）。多群各记一局，分钟数只能加一次。把排行塞进 `SessionService`，会逼会话服务理解「跨群去重」，破坏「只有 session 能 close」的边界。
2. **`_recorded_quit_cache` 是排行写入的幂等，不是会话幂等。** 会话已经用 `session_id` 保证 `_record_session` 不重复。5 分钟窗口专门挡「同一 sid 在多个群几乎同时 `_on_closed`」把 `play_records` 加两遍。缓存必须和 `play_records` 写路径绑在一起，进程内即可，重启丢了最多短窗口内多记一笔，可接受。
3. **persistence 只负责 JSON 和过期裁剪，不负责规则。** 日界、去重窗口、聚合排序是应用规则。Mixin 继续提供 `_load/_save_play_records` 可以，但应改成读写服务交出的 dict，或由服务调用 store 接口。不要让 Mixin 继续当 `_record_playtime` 的家。
4. **Web 和命令都是读者。** `admin_api` / `statistics.py` 不要再 `plugin.play_records[...]`。读 `ranking.play_records` 或更好：`ranking.minutes_between(...)` / `ranking.sessions_for(sid)`。第一刀允许只把属性挪到服务上，Web 改指向。

### 3.3 推荐对象

```python
class RankingService:
    play_records: dict          # 日聚合，落盘
    session_records: dict       # 时间片，落盘
    _recorded_quit_cache: dict  # 进程内，不落盘
    rank_push_groups / rank_push_all / last_push_date  # 推送范围可第二刀再搬

    def day_key(self, offset_days=0) -> str: ...
    def record_closed(self, sid, gameid, game_name, started_at, ended_at, duration_min, group_id) -> None: ...
    def aggregate(self, days, sids) -> list[RankRow]: ...
```

`SessionService._on_closed` 只调：

```python
self._ranking.record_closed(...)
```

不再 `plugin._record_playtime` + `plugin._record_session`。`record_closed` 内部：先 session_id 去重写 `session_records`，再按 5 分钟缓存决定是否累加 `play_records`。

`rank_push_*` 是通道策略（哪些群几点推），可以暂时留在插件；和分钟账本分开。不要把 PNG 渲染放进 `RankingService`。

### 3.4 明确不选的方案

| 方案 | 为什么否 |
| --- | --- |
| 继续挂在插件 `self` 上 | 命令、会话、Web、Mixin 四面写同一 dict，拆 commands 时还会被拖走 |
| 交给 `SessionService` | 会话按群，排行按人；去重语义不同 |
| 交给 `PersistenceMixin` | Mixin 不该有 5 分钟业务窗口 |
| 拆成 Ranking 与 History 两个服务 | 同一写入点、同一日界，先一个服务；等甘特图规则独立演化再拆 |
| 用 Redis TTL 做去重 | 已拍板：单进程 JSON，进程内 deadline/缓存即可 |

## 4. 和命令层拆分的衔接

`command-layer-split.md` 第 1 步里的「价格查询」和第 2 步「排行榜记账」按本文收口：

- 价格：一个 `PriceQueryService` 用例，不包 `resolve_steam_input`，不重复 client 里的区回退。
- 排行：先把三份状态搬进 `RankingService`，再搬 `/steam rank` 胶水。没有持有权决策就搬 `rank_cmd.py`，命令层仍会伸手进 `self.play_records`。

落地顺序建议：

1. `RankingService` 接管 `record_closed` + `aggregate` + 日界（行为不变，单测从 fake plugin 方法改为打服务）。
2. `PriceQueryService.build_card` 抽出，命令只负责候选会话和出图；顺手让 `/steam game` 复用。
3. 再考虑 `presentation/commands/store.py` 与 `rank.py`。
