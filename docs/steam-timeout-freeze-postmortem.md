# Steam 超时拖死轮询与通知渲染复盘

**发现日期**：2026-09-09  
**修复提交**：`429079d`（`fix: 避免 Steam 超时把轮询和通知渲染拖死`）  
**严重级别**：恶性。Steam 网络抖动时，插件会把 AstrBot 主事件循环拖进数分钟等待，表现为 Bot 假死、命令无响应、结束卡迟到或丢失。

本文记录这次问题的真实机制、误判、修复边界和验证方式。它不替代 `REFACTORING.md`，也不覆盖 `SessionService` 并发竞态（见 `docs/concurrency-safety-issue.md`）。

---

## 1. 用户可见现象

Steam API 超时、连不上或批量查询失败时，会出现：

1. AstrBot 长时间不响应命令，看起来像整个 Bot 卡死。
2. 主轮询这一轮走不完，后续状态检测、排行榜、结束卡都往后堆。
3. 玩家已经退出游戏，结束通知要等很久才发，甚至被下一局开始卡挤掉观感。
4. 网络恢复后，插件可能突然补发积压通知，或漏掉本轮状态变化。

现场容易把所有症状都归因成「同步 `httpx.get()` 卡住事件循环」。这次复盘后可以拆成两条独立路径，严重程度不同，不能混为一谈。

---

## 2. 结论先说

这次恶性 bug 是两条路径叠在一起：

| 路径 | 代码位置 | 实际机制 | 最坏影响 |
| --- | --- | --- | --- |
| A. 批量失败后串行单查 | `fetch_player_statuses_batch` → `fetch_player_status`；`check_status_change` 在 `status_override is None` 时再打网 | 都是 `await AsyncClient.get()`，**不冻死整个事件循环**，但会把**主轮询这一轮**拖进数分钟 | 本轮检测、到期结算、结束卡全部迟到 |
| B. 刷开始/结束卡时同步拉图 | `game_start.get_avatar_path` / `game_end.get_avatar_path` 里的同步 `httpx.get` | **真的阻塞事件循环** | 每次未命中缓存的头像下载最多卡住 Bot 约 10 秒 |

路径 A 是这次用户反馈的主因：批量查询失败后「兜底单查」把 30+ 人串起来重试。  
路径 B 是二次伤害：主轮询为了避免超时堵住结束卡，会在拉 Steam 状态期间每秒 `_flush_pending_end_notifications()`；如果这时走同步拉头像，就会把「本想保活」的 tick 变成真卡死。

修复原则：

> 漏一轮状态可以接受，冻死 AstrBot 不可接受。

---

## 3. 调用链

主轮询每分钟大致走：

```text
_poll_loop
  tick_due()                         # 先结算到期会话
  _flush_pending_end_notifications() # 立刻刷结束卡，避免被后续 Steam 超时拖住
  _fetch_statuses_while_ticking(all_sids)
      fetch_player_statuses_batch(all_sids)     # 一次批量，timeout=15s，带重试
      每秒 tick_due + flush 结束卡              # 本意是超时期间仍能发卡
  各群 check_status_change(sid, status_override=batch[sid])
  再 flush 开始卡 / 结束卡
```

关键文件：

- `src/application/services/polling_tracking.py`
- `src/infrastructure/clients/steam.py`
- `src/application/services/status_change_tracking.py`
- `src/application/services/notification_tracking.py`
- `src/presentation/renderers/game_start.py`
- `src/presentation/renderers/game_end.py`

---

## 4. 路径 A：批量失败后的串行单查

### 4.1 旧逻辑

`fetch_player_statuses_batch` 一批 `ConnectError` / `ReadTimeout` / 非 200 重试耗尽后，会对**本批每一个缺失 sid** 再调：

```python
await self.fetch_player_status(sid, retry=1)
```

`fetch_player_status` 默认 `timeout=15`，失败再指数退避重试。批量失败时传入 `retry=1`，单次仍可能卡满 15 秒。

更糟的是检测循环：

```python
status = status_override if (single_sid and status_override is not None) else await self.fetch_player_status(sid)
```

轮询本来就会把批量结果里**没有这个人**写成 `status_override=None`。旧判断把 `None` 当成「没传覆盖值」，于是每个人再打一次默认 `retry=3` 的单查。

### 4.2 最坏耗时

以 36 人、Steam 彻底连不上为例（数量来自本地实测规模，不是理论上限）：

1. 批量请求：`timeout=15` × 默认重试次数，再加退避睡眠。
2. 批量失败后串行单查：`36 × 15s ≈ 9 分钟`。
3. `check_status_change` 再对 `None` 补打网：每人最多再 3 次 15s。

这些 `await` 发生在主轮询协程里。其它纯 I/O 协程理论上还能跑，但：

- 本轮 `check_status_change` 走不完；
- 下一轮 40 秒节奏被撑破；
- `tick_due` 虽然在拉状态期间每秒会跑，结束卡却可能卡在同步渲染上（路径 B）；
- 用户体感就是 Bot 死了。

### 4.3 为什么当初会写成这样

批量查询的本意是减少 `GetPlayerSummaries` 次数、避开 Steam 限流。失败后单查是「尽量不漏人」的兜底。这个兜底默认假设：

- 批量失败是偶发；
- 单查很快能成功；
- 漏状态比多等几秒更糟。

真实故障是对端超时或 DNS/代理失败。这时单查和批量走同一条坏网络，成功率不会更高，只会把等待时间乘上人数。

---

## 5. 路径 B：同步 `httpx.get` 拉头像

### 5.1 旧逻辑

`render_game_start` / `render_game_end` 是 async，但头像下载是同步函数：

```python
def get_avatar_path(...):
    resp = httpx.get(url, timeout=10, proxy=proxy)
```

`game_end.py` 里还有一份重复实现。横版封面 `get_horizontal_cover_path` 里也有同步 `httpx.get`，但旧代码先判断未赋值的 `resp`，被 `except` 吃掉，**实际上不会卡满 10 秒**，只是死代码。

头像 24 小时缓存命中时不走网络，所以平时不一定爆。爆的条件是：

- 新玩家 / 缓存过期 / 强制刷新；
- Steam CDN 慢或超时；
- 主轮询正在 `_fetch_statuses_while_ticking` 里每秒 flush 结束卡。

### 5.2 和路径 A 的耦合

`_fetch_statuses_while_ticking` 的设计是：批量 Steam 请求可能要 15 秒以上，所以每秒继续 `tick_due` 并刷结束卡，避免「人已经退出，卡却要等 Steam 超时才发」。

如果 flush 时走同步 `httpx.get`：

```text
主循环 await flush
  → 同步 httpx.get 头像（最多 10s）
  → 事件循环线程被占住
  → 命令、其它插件、甚至 tick 自己都停
```

这才是「整个 AstrBot 卡住」的硬证据。路径 A 负责把等待拉长，路径 B 负责把等待变成真阻塞。

早期检查报告把横版封面的死代码也标成高危同步阻塞，这点不成立；正确优先级是把头像/封面改成 async，而不是补上那次缺失的同步 `httpx.get`。

---

## 6. 修复做了什么

### 6.1 批量失败不再串行单查

`fetch_player_statuses_batch` 本批重试耗尽后直接跳过，缺失 sid 不进返回字典，并打 error 日志说明「不再串行单查」。

`fetch_player_status` **保留**。`/steamwho`、绑 ID、测试渲染卡、openbox 摘要仍然要查单人。删掉的是检测循环里的兜底，不是单查这个能力。

### 6.2 检测循环只吃本轮批量结果

`check_status_change` 用哨兵区分「没传」和「传了 None」：

```python
_STATUS_UNSET = object()

async def check_status_change(..., status_override=_STATUS_UNSET, ...):
    if not single_sid or status_override is _STATUS_UNSET:
        # 跳过，禁止打网
        ...
    status = status_override
    if not status:
        # 批量没有这个人，也跳过，禁止打网
        ...
```

规则：

- 调用方必须传入本轮 `status_override`；
- 没传：跳过；
- 传了 `None` / 空：跳过；
- 不再自己 `fetch_player_status`。

代价是这一轮这个人的状态变化会被漏掉。网络恢复后下一轮批量成功即可追上。会话状态机不会把「获取失败」当成退出。

### 6.3 头像和横版封面改为 AsyncClient

- `get_avatar_path`、`get_horizontal_cover_path` 改为 `async`，内部 `await AsyncClient.get()`。
- 结束卡删除重复的同步头像函数，复用开始卡实现。
- 顺手修好横版封面 `resp` 未赋值的死代码，现在会真正去拉 Store `appdetails` 和 `header_image`。

刷卡期间即使 CDN 慢，也只是这个协程在等，不再堵住事件循环。

---

## 7. 验证

新增 / 调整的测试：

- `tests/unit/test_steam_batch.py`  
  批量 `ConnectError` 后返回 `{}`，且 `fetch_player_status` 一次都不调用。
- `tests/unit/test_status_change_quit.py`  
  `status_override=None` 和完全不传 `status_override` 都不得打网。
- `tests/unit/test_avatar_download.py`  
  头像、横版封面走 `AsyncClient`；缓存命中不打网。
- `tests/unit/test_session_service.py`  
  检测路径补上 `status_override`，避免测试再走已删除的单查兜底。

这些测试锁住的是「失败不得再放大等待」，不是「网络永远成功」。

---

## 8. 仍然存在的风险

1. **漏一轮状态**  
   Steam 整批失败时，本轮所有人都不更新。短时抖动可接受；若 API Key 无效或持续限流，会连续多轮空白，需要靠日志 `[批量查询] 本批彻底失败` 发现。
2. **命令路径仍会单查**  
   `/steamwho` 等仍走 `fetch_player_status`。这是用户主动触发，人数为 1，不会按监控列表放大。
3. **`SessionService` 并发竞态未在本次修复**  
   同一 sid 多群并行 `handle` / `tick_due` 仍缺锁。见 `docs/concurrency-safety-issue.md`。
4. **渲染里仍有同步磁盘 IO**  
   `open(..., "wb")` 写头像/封面是短同步调用，正常体积可忽略，不在这次范围内。
5. **本地 `docs/` 默认被 `.gitignore` 忽略**  
   本文需要 `git add -f` 才能进仓库。后续同类复盘同样要强制加，或把规则改成只忽略未跟踪的设计稿。

---

## 9. 教训

1. **失败兜底要看放大系数。**  
   「批量失败就逐个再试」在成功路径上合理，在超时路径上会把 15 秒变成人数 × 15 秒。兜底必须有总预算，或者直接跳过。
2. **`None` 不能同时表示「没传」和「查失败」。**  
   轮询把缺失写成 `None`，检测函数却把 `None` 当成需要单查。哨兵值或必填参数才能切开。
3. **async 函数里不能藏同步 HTTP。**  
   轮询用 `AsyncClient` 不代表渲染也安全。开始/结束卡在主循环的 flush 路径上，同步 `httpx.get` 会把保活 tick 变成假死。
4. **先分清「协程等 I/O」和「线程被阻塞」。**  
   前者拖死的是这一轮业务；后者拖死的是整个 Bot。两者日志长得像，修复完全不同。
5. **不要为了「看起来完整」去补死代码。**  
   横版封面缺了同步 GET，才没有真阻塞。正确修复是改 async，不是把同步调用补全。

---

## 10. 相关代码

修复后的关键行为：

```134:137:src/infrastructure/clients/steam.py
                        logger.error(
                            f"[批量查询] 本批彻底失败，跳过本轮（不再串行单查，避免把主循环拖死）: "
                            f"{len(batch)} 个 ID"
                        )
```

```26:33:src/application/services/status_change_tracking.py
            # 检测循环只消费本轮批量结果；没有覆盖值就跳过，禁止打网单查。
            if not single_sid or status_override is _STATUS_UNSET:
                logs.append(f"{sid}: 获取失败")
                continue
            status = status_override
            if not status:
                logs.append(f"{sid}: 获取失败")
                continue
```

```23:37:src/presentation/renderers/game_start.py
async def get_avatar_path(data_dir, steamid, url, force_update=False, proxy=None):
    ...
        async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(proxy)) as client:
            resp = await client.get(url)
```
