# ADR：删除透传中介并内联单次私有辅助

- 状态：已落地
- 日期：2026-09-12
- 范围：`src/` 内死转发、一层改名包装、仅服务一个调用点的短私有函数
- 对照：本地审计清单已归档到 `docs/self/helper_inline_analysis.md`（不入库）；本文记录落地决策，不以缩短文件为目的

本文不替代 `REFACTORING.md`。那份文档记录模块拆分与兼容边界；本文只覆盖 **「空壳转发是否值得保留」**。

## 1. 背景

模块拆分之后，出现两类噪音：

1. **透传中介**：函数自身不做计算、校验、IO、锁或缓存，只把参数转给另一个对象。
2. **单次私有辅助**：名称以 `_` 开头，生产代码外部调用点 ≤ 1，体量小，内联后不会把调用方撑爆。

它们增加跳转成本，却不构成可独立测试的边界。审计先只出报告，确认后再改代码。

判定口径：

- 列入：死代码、兼容包装、一层改名、一行路径函数、与另一条路径重复的 closed 分发。
- 不列入：dunder、AstrBot `@filter.command` 注册桩、HTTP 客户端公开 REST 方法、带懒初始化/锁/缓存/重试的包装、经 `getattr` 调用的领域规则。

## 2. 决策

**内联优先于再拆一层。** 只删没有业务边界的符号；命令注册、会话所有权、字体解析本体保持原位。

落地分三类：

| 类别 | 动作 |
| --- | --- |
| 死转发 / 兼容包装 | 删除，调用方改用真实函数 |
| 一层改名 | 并进共享入口或唯一调用方 |
| 单次短辅助 | 内联；若两条路径共享同一副作用，抽一个同步函数，而不是再留一条空壳路径 |

明确不做：

- 不为了「看起来更干净」去内联有 HTTP、锁、缓存、映射或校验的函数。
- 不把 `tick_due` 改成 `async` 来复用 `_dispatch`。
- 不在没有 AstrBot command alias 的前提下合并命令 handler。

## 3. 落地内容

### 3.1 删除的死转发

| 符号 | 原位置 | 处理 |
| --- | --- | --- |
| `to_cny` | `src/shared/utils/price.py` | 删除。生产 0 次调用，体为 `convert(..., "CNY")`。 |
| `summary_to_cny` | 同上 | 删除。测试改为 `summary_to_currency(summary, "CNY")`。 |
| `SteamClientMixin.fetch_game_reviews` | `src/infrastructure/clients/steam.py` | 删除。生产 0 次；价格卡走 `fetch_game_reviews_both`。 |
| `get_font_path`（game_end 副本） | `src/presentation/renderers/game_end.py` | 删除。无人调用的同文副本。 |
| `get_font_path`（game_start） | `src/presentation/renderers/game_start.py` | 删除。调用方改 `resolve_font_path`。 |
| `PersistenceMixin.get_font_path` | `src/infrastructure/persistence/plugin_data.py` | 删除。默认字体名已由 `resolve_font_path` 处理。 |
| `MonitorAdminService.resolve_steam_input` | `src/application/services/monitor_admin.py` | 删除。Web 后台本来就调 `plugin.resolve_steam_input`。 |
| `WebAdminAPI.invalidate_cache` | `src/presentation/web/admin_api.py` | 删除。唯一调用方直接 `self._response_cache.invalidate(...)`。 |

字体解析现在只有一条路径：`src/shared/fonts.py` 的 `resolve_font_path`。插件主体、通知、成就、管理页都直接调用它。

### 3.2 内联的单次辅助

| 符号 | 并入 |
| --- | --- |
| `_error_missing_once` | `resolve_font_path` |
| `_heatmap_group_sids` | `build_heatmap_data` |
| `_pick_pending` | `hydrate_from_legacy` |
| `_blacklist_path` / `_blacklist_verified_flag_path` / `_mark_blacklist_verified` | `achievement_monitor` 的 load/save/`verify_blacklist_once` |
| `_get_groups_file_path` | `_load_group_steam_ids` / `_save_group_steam_ids` |
| `_dispatch_sync` | 见 3.3 |

`_is_https`、`_existing_file` 有多次调用，收益小，未动。

### 3.3 会话 closed 路径

`_dispatch`（async，给 `handle`）和 `_dispatch_sync`（sync，给 `tick_due`）对 `kind == "closed"` 做同一件事：`_on_closed` + 清 `_meta`。

备选方案：

1. 把 `tick_due` 改成 async，直接 `await _dispatch`。
2. 在 `tick_due` 里复制 closed 分支。
3. 抽出同步的 `_apply_closed`，两边共用。

选 3。`tick_due` 由主轮询同步调用，改 async 会把 await 传播进检测循环；复制分支会再养出第三条路径。`_apply_closed` 不是透传，它封装「关闭会话时必须一起做的两步」。

## 4. 明确保留

| 符号 | 原因 |
| --- | --- |
| `SessionQuitMixin` | 审计时被标成空类，实际持有 `session_service` 懒初始化和结束卡文案 `_end_game_tip`。删掉会拆掉插件 MRO 上的会话入口。 |
| `steam_price` / `steam_px` / `steam_zai_gan_ma` / `steam_qq_menu_*` | AstrBot 每个 `@filter.command` 需要独立函数。一行 `async for` / `yield` 是注册桩，不是业务中介。没有框架 alias 就保持现状。 |
| `QQOfficialPanelClient.get_panel` / `delete_panel` | 客户端公开 REST 面，对称 GET/DELETE，不是内部空壳。 |
| `main.Main` | AstrBot 发现入口。 |
| `SessionService._sessions` / `_meta` / `_key` / `_store` | 多次调用，且 `_sessions`/`_meta` 有懒初始化。 |
| `_should_skip_game` | AST 会看成 0 次，实际经 `getattr` 调用，含黑白名单规则。 |

## 5. 后果

- 调用链变短：字体、价格折算、管理页缓存失效不再经过改名包装。
- 行为不变：`resolve_font_path` 仍负责默认字体名和缺失日志去重；`tick_due` 仍同步。
- 测试：`test_store_region_fallback` 改为直接调 `summary_to_currency`。相关单测（会话、价格区回退、模块结构、Web 缓存、字体包）通过。
- 体积：约 −58 行（15 个文件，+67 / −125）。

后续若 AstrBot 支持同一 handler 多 command，再考虑合并 `steam_price` / `steam_px`。`SessionQuitMixin` 只有在结束卡文案和 `session_service` 属性迁走之后才具备删除条件。

## 6. 验证

```text
python -m unittest tests.unit.test_store_region_fallback tests.unit.test_session_service tests.unit.test_confirm_quit tests.unit.test_status_change_quit tests.unit.test_modular_structure tests.unit.test_web_performance tests.unit.test_price_search tests.unit.test_font_pack -q
```
